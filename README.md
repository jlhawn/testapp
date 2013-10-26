testapp
=======

Examples
--------

Submit a job using a POST request with a list of URLs.

```shell
curl -X POST -H "Content-Type: application/json" -d @- http://testapp-jlhawn.dotcloud.com/ <<-EOF
    {"urls": ["http://news.ycombinator.com/", "http://www.dotcloud.com/"]}
EOF
```
Response:

```json
{"job_id": "526c17fd332da100a718eeec"}
```

You can check the status with a request to /status/`<job_id>`

```json
{"status": "Working", "job_id": "526c17fd332da100a718eeec", "completed": 51, "processing": 12, "waiting": 214, "images_found": 738}
```

When the status is "Finished", you can get the results from /result/`<job_id>`
See example_results.json for an example of job results.

If you request the result of a job using a browser, the web server will return an html document. View these for an example:

* Hacker News
    * http://testapp-jlhawn.dotcloud.com/result/526c0780332da100a718eeea
* dotCloud
    * http://testapp-jlhawn.dotcloud.com/result/526c121b332da100a9b4df85
* CNN (many images)
    * http://testapp-jlhawn.dotcloud.com/result/526c1288332da100a866b150


Design Decisions
-----------------

I ended up going with dotCloud to host the web server and the database. dotCloud PaaS was really easy to setup and start using and it looks very easy to scale up the number of web servers or add shards to my Mongo cluster.

For the distributed queue service, I ended up going with PiCloud Queues. PiCloud Queues are much more customizable than IronWorkers from iron.io and also has an iterative approach to handling payloads rather than spawning one process per payload.

I went with MongoDB over a RDBMS like Postgres simply for the ease of use given by the pymongo client library. It also looks relatively simple to scale Mongo horizontally. I did have an issue with pymongo bulk insertsion though, and there's more detail on that in the code comments


Shortcomings/Possible improvements
----------------------------------

I can't guarantee that my scraping method is thorough. If I request too many pages in a short time, a worker can be rate-limited and the worker ends up skipping that url and therefore any images on that page. I'm only gathering images and links from `<img>` and `<a>` tags, so there may be several links that I'm missing because they are only plaintext and there may be several images that I'm missing because they are only referenced in CSS or JavaScript.

Also, you may notice that workers can quickly process hundreds of links, but the last dozen or so links can take a while to process. This is because the PiCloud jobs pop several messages off the queue at once then process them, requiring fewer round trips to the queue service, but this could lead to a situation where one job has popped off the last 10 messages, only processing one, while a dozen other jobs have nothing else to get from the queue.

I also thought it was cool that you can view the results page in a browser and have it display all of the images, but on result sets with a very large number of images or just a handlful of GIFs, a browser can become very slow. A better way to do it would be to load images dynamically as you scroll, but I'll leave that as an exercise for later.


Design Notes
------------

Web Server: Flask

```
POST -> List of URLs -> JobID -> Client
                    |
                     -> emit (JobID, URL) pairs -> Database/Queue

status/JobID
    Return counts (JSON):
        given URLs
        URLs to crawl
        URLs being crawled
        crawled URLs
        images found

result/JobID
    Return JSON Object of lists of images.
    Key: Webpage URL
    Value: List of Image URLs

    (Fancy web-page of images to a browser?)
```

Database
--------

AWS RDS? MongoDB?

````
Schema ideas

--------
jobs
--------
id                  (int, primary_key)
given_url_count     (int)
urls_to_crawl       (int)
urls_being_crawled  (int)
crawled_urls        (int)
crawl_depth         (int)

------------
urls
------------
job_id      (int, foreign_key)
url         (string)

--------
images
--------
job_id      (int, foreign_key)
image_url   (string)
    ```

Queueing System
---------------

PiCloud CloudQueues? iron.io IronWorker/MQ?

```
Messages: JSON Objects
    job_id
    crawl_depth
    url
```