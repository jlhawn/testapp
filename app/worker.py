import cloud
import logging
import pymongo
import urllib2
import urlparse

from lxml import etree
from pymongo.errors import DuplicateKeyError

PICLOUD_API_KEY = 0000
PICLOUD_API_SECRETKEY = '*******************'
MONGO_HOST = 'testapp-jlhawn-data-0.azva.dotcloud.net'
MONGO_PORT = 2778
MONGO_WORKER_USERNAME = '*********'
MONGO_WORKER_PASSWORD = '*********'


def resource_is_image(url):
    """
    Naively determines whether the given resource represents an image of
    type PNG, JPG, or GIF.
    """
    return url.endswith('.png') or url.endswith('.jpg') or url.endswith('.gif')


def validate_resource(url):
    """
    We want to ensure that urls are http(s) only and don't contain extraneous
    info like query parameters or an index.
    """
    parsed = urlparse.urlparse(url)

    if parsed.scheme not in ['http', 'https']:
        return None

    return '%s://%s%s' % (parsed.scheme, parsed.netloc, parsed.path)


class CrawlerWorker(object):

    def pre_handling(self):
        """
        Called before processing any messages from the queue.
        Sets up logging and MongoDB connection for this worker.
        """
        logging.basicConfig(level=logging.DEBUG)
        self.logger = logging.getLogger('CrawlerWorker')

        self.mongo_client = pymongo.MongoClient(MONGO_HOST, MONGO_PORT)
        self.crawlerdb = self.mongo_client.crawlerdb
        self.crawlerdb.authenticate(
            MONGO_WORKER_USERNAME, MONGO_WORKER_PASSWORD
        )

    def parse_webpage(self, url):
        """
        Downloads and parses the content for the given resource.
        Returns a list of links and images found on the webpage.
        """
        # download web content
        content = urllib2.urlopen(url)
        
        content_type = content.headers.getheader('Content-Type')
        self.logger.debug('Content-Type: %s', content_type)

        if content_type.find('text/html') < 0:
            # We're only interested in web pages.
            return [], []

        # parse webpage
        parser = etree.HTMLParser(remove_comments=True)
        root_node = etree.fromstring(content.read(), parser)

        # grab more links and images
        links = []
        images = []

        # Check anchor tags.
        for element in root_node.iterdescendants('a'):
            href = element.attrib.get('href')

            if href is None:
                continue

            # Join with page location (handles relative/absolute links).
            parsed_link = urlparse.urljoin(content.url, href)
            parsed_link = validate_resource(parsed_link)

            if parsed_link is None:
                continue

            if resource_is_image(parsed_link):
                images.append({'image_url': parsed_link})
            else:
                links.append({'url': parsed_link})

        # Check image tags.
        for element in root_node.iterdescendants('img'):
            src = element.attrib.get('src')

            if src is None:
                continue

            # Join with page location (handles relative/absolute links).
            parsed_src = urlparse.urljoin(content.url, src)
            parsed_src = validate_resource(parsed_src)

            if parsed_src is None:
                continue

            if resource_is_image(parsed_src):
                images.append({'image_url': parsed_src})

        return links, images

    def message_handler(self, url_info):
        """
        Process the URL info that was just removed from the queue.
        Handles downloading the resource, parsing for images/links, and
        adding new images/links to the datastore if appropriate. Also enqueues
        more URLs if crawl_depth > 0.
        """
        job_id = url_info['job_id']
        crawl_depth = url_info['crawl_depth']
        url = url_info['url']

        self.logger.debug('Handling new message: %s', url_info)

        # Update job info counts.
        self.crawlerdb.jobs.update(
            {'_id': job_id},
            {'$inc': {'urls_to_crawl': -1, 'urls_being_crawled': 1}},
        )

        try:
            links, images = self.parse_webpage(url)
        except Exception as e:
            self.logger.exception('url: %s - %s', url, e)
            links, images = [], []

        # Add job_id to info.
        for link_info in links:
            link_info['job_id'] = job_id

        for image_info in images:
            image_info['job_id'] = job_id

        new_links = []

        # Add new links if appropriate.
        if crawl_depth > 0:

            # pymongo does have a bulk insert method and it has
            # continue_on_error which would avoid inserting duplicates.
            # However, it DOES NOT tell me which ones were the duplicates so
            # that I can filter them out, so I've decided to insert them
            # one at a time at the cost of one RTT to DB each.
            for new_link_info in links:
                try:
                    self.crawlerdb.urls.insert(new_link_info)
                    new_link_info['crawl_depth'] = crawl_depth-1
                    new_links.append(new_link_info)
                except DuplicateKeyError:
                    # This (job, url) pair has been seen before.
                    # It wont be added to the queue.
                    pass

        new_images_count = 0

        # Add new images to mongo.
        for new_image_info in images:
            try:
                # See comment above on why I'm not using bulk-insert.
                self.crawlerdb.images.insert(new_image_info)
                new_images_count += 1
            except DuplicateKeyError:
                # This (job, image_url) pair has been seen before.
                # It wasn't added to the data store.
                pass

        # Update job info counts.
        self.crawlerdb.jobs.update(
            {'_id': job_id},
            {'$inc': {
                'urls_to_crawl': len(new_links),
                'images_found': new_images_count,
                'urls_being_crawled': -1,
                'urls_crawled': 1,
            }},
        )

        # Queue any new links.
        return new_links


def setup_cloud_queue():
    """
    Attaches an instance of CrawlerWorker to a Queue on PiCloud.
    Worker jobs will run using the s1 core-type (AWS t1.micro) in order to
    avoid being rate-limited. We can scale horizontally by increasing the
    max_parallel_jobs value.
    """
    cloud.setkey(PICLOUD_API_KEY, PICLOUD_API_SECRETKEY)

    url_queue = cloud.queue.get('url_queue')

    url_queue.attach(
        CrawlerWorker(), output_queues=[url_queue], iter_output=True,
        _type='s1', _env='dotcloud_testapp', max_parallel_jobs=20,
    )

def ensure_mongo_index():
    """
    Ensures a unique index on the URLs and images collections in order to
    avoid duplicate insertions and improve query performance.
    """
    mongo_client = pymongo.MongoClient(MONGO_HOST, MONGO_PORT)

    crawlerdb = mongo_client.crawlerdb
    crawlerdb.authenticate(MONGO_WORKER_USERNAME, MONGO_WORKER_PASSWORD)

    crawlerdb.images.ensure_index(
        [('job_id', pymongo.ASCENDING), ('image_url', pymongo.ASCENDING)],
        unique=True,
    )
    crawlerdb.urls.ensure_index(
        [('job_id', pymongo.ASCENDING), ('url', pymongo.ASCENDING)],
        unique=True,
    )
