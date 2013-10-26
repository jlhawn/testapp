import cloud
import flask
import bson
import json
import pymongo

from jinja2 import Environment, FileSystemLoader

PICLOUD_API_KEY = 0000
PICLOUD_API_SECRETKEY = '****************************'
MONGO_HOST = 'testapp-jlhawn-data-0.azva.dotcloud.net'
MONGO_PORT = 2778
MONGO_WEB_USERNAME = '**************'
MONGO_WEB_PASSWORD = '**************'

cloud.setkey(PICLOUD_API_KEY, PICLOUD_API_SECRETKEY)

mongo_client = pymongo.MongoClient(MONGO_HOST, MONGO_PORT)

crawlerdb = mongo_client.crawlerdb
crawlerdb.authenticate(MONGO_WEB_USERNAME, MONGO_WEB_PASSWORD)

app = flask.Flask('testapp')

template_dirs = ['templates','/home/dotcloud/current/app/templates']
template_env = Environment(loader=FileSystemLoader(template_dirs))


def still_processing(job):
    """
    Returns whether a job is still processing which is determined by the fact
    that the job is either currently crawling a page or has pages waiting to
    be crawled.
    """
    return job['urls_to_crawl'] + job['urls_being_crawled'] > 0


@app.route('/', methods=['POST'])
def submit_job():
    """
    Expects request data in the form of a JSON object with a list of urls to
    crawl and (optionally) a crawl_depth (default: 1). Creates a Job and
    submits the first set of urls to the worker queue to be processed.

    Returns a JSON object containing the job id.
    """
    request = flask.request

    request_data = request.get_json()
    crawl_depth = request_data.get('crawl_depth', 1)
    given_urls = request_data.get('urls', [])

    job_data = {
        'crawl_depth': crawl_depth,
        'given_url_count': len(given_urls),
        'urls_to_crawl': len(given_urls),
        'urls_being_crawled': 0,
        'urls_crawled': 0,
        'images_found': 0,
    }

    # job_id is a MongoDB ObjectID.
    job_id = crawlerdb.jobs.insert(job_data)

    job_urls = [{'job_id': job_id, 'url': url} for url in given_urls]

    crawlerdb.urls.insert(job_urls)

    for url_info in job_urls:
        url_info['crawl_depth'] = crawl_depth

    url_queue = cloud.queue.get('url_queue')
    url_queue.push(job_urls)

    return json.dumps({'job_id': str(job_id)})


@app.route('/status/<job_id>')
def status(job_id):
    """
    Returns a JSON object representing the status of the job referenced by the
    given job id.
    """
    obj_id = bson.ObjectId(job_id)
    job = crawlerdb.jobs.find_one({'_id': obj_id})

    if job is None:
        return json.dumps({'status': 'No such job'})
    
    status_info = {
        'job_id': job_id,
        'waiting': job['urls_to_crawl'],
        'processing': job['urls_being_crawled'],
        'completed': job['urls_crawled'],
        'images_found': job['images_found'],
    }

    status_info['status'] = 'Working' if still_processing(job) else 'Finished'

    return json.dumps(status_info)


@app.route('/result/<job_id>')
def result(job_id):
    """
    If the job is completed, gathers a list of all images resulting from the
    job. Based on whether the client is a browser, it will either return an
    html document containing the images or simply a JSON object with the list
    of image URLs.
    """
    obj_id = bson.ObjectId(job_id)
    job = crawlerdb.jobs.find_one({'_id': obj_id})

    if job is None:
        return json.dumps({'status': 'No such job'})

    if still_processing(job):
        return json.dumps({'status': 'Job not finished'})

    result_info = {
        'status': 'Finished',
        'job_id': job_id,
        'images': [],
    }

    for image in crawlerdb.images.find({'job_id': obj_id}):
        result_info['images'].append(image['image_url'])

    request = flask.request

    if request.user_agent.browser:
        # Client is a web browser. We'll give it HTML.
        template = template_env.get_template('result.html')
        return template.render(**result_info)

    return json.dumps(result_info)


if __name__ == '__main__':
    app.debug = True
    app.run('localhost', 8080)
