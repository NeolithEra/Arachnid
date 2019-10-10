import requests
import arachnid_enums
import random
import os
from timewidgets import Timer

from . import responseparser
from .scheduler import Scheduler, FuzzingOptions
from .scraper import Scraper
from .domaindata import DomainData
from .crawler_url import CrawlerURL
from . import url_functions
from . import warning_issuer

this_dir = os.path.dirname(os.path.abspath(__file__))


class CrawlerConfig:
    def __init__(self):
        self.set_default()

    def set_default(self):
        self.scrape_links = True
        self.scrape_subdomains = True
        self.scrape_phone_number = True
        self.scrape_email = True
        self.scrape_social_media = True
        self.documents = {"doc", "docx", "ppt", "pptx", "pps", "xls", "xlsx", "csv", "odt", "odp", "pdf", "txt",
                          "zip", "rar", "dmg", "exe", "apk", "bin", "rpm", "dpkg"}
        self.obey_robots = True
        self.allow_query = True
        self.agent = arachnid_enums.Agent.FIREFOX.value
        self.custom_str = None
        self.custom_str_case_sensitive = False
        self.custom_regex = None
        self.default_delay = arachnid_enums.Delay.NONE.value
        self.paths_list_file_loc = os.path.join(this_dir, "data/fuzz_list.txt")
        self.subs_list_file_loc = os.path.join(this_dir, "data/subdomain_fuzz_list.txt")
        self.fuzz_paths = False
        self.fuzz_subs = False
        self.blacklisted_directories = []

    def set_stealth(self):
        self.obey_robots = True
        self.agent = arachnid_enums.Agent.GOOGLE.value
        self.default_delay = arachnid_enums.Delay.HIGH.value
        self.fuzz_paths = False
        self.fuzz_subs = False

    def set_aggressive(self):
        self.obey_robots = False 
        self.default_delay = arachnid_enums.Delay.NONE.value
        self.fuzz_paths = True
        self.fuzz_subs = True

    def set_layout_only(self):
        self.scrape_subdomains = False
        self.scrape_phone_number = False 
        self.scrape_email = False
        self.scrape_social_media = False
        self.documents = {}
        self.custom_str = None
        self.custom_regex = None


class Crawler:
    def __init__(self, seed, config=CrawlerConfig()):
        seed = CrawlerURL(seed)
        self.config = config
        fuzzing_options = FuzzingOptions(config.paths_list_file_loc if config.fuzz_paths else None,
                                         config.subs_list_file_loc if config.fuzz_subs else None)
        self.schedule = Scheduler(seed, useragent=self.config.agent,
                                  fuzzing_options=fuzzing_options,
                                  respect_robots=self.config.obey_robots,
                                  allow_subdomains=self.config.scrape_subdomains,
                                  blacklist_dirs=self.config.blacklisted_directories)
        self.output = DomainData(seed.get_netloc())
        self.output.start()
        self.output.add_config(self.config)
        self.delay_sw = Timer()
        self._update_crawl_delay()
        self.delay_sw.start()

    def crawl_next(self):
        c_url = self.schedule.next_url()
        if c_url is None:
            self.finish()
            return False
        print(c_url)
        self.delay_sw.wait()
        try:
            r = requests.get(c_url.get_url(), headers={"User-Agent": self.config.agent}, timeout=30)
            warning_issuer.issue_warning_from_status_code(r.status_code, c_url.get_url())
            if "content-type" in r.headers.keys():
                if "text/html" in r.headers["content-type"]:
                    self._parse_page(r, c_url)
                else:
                    self._parse_document(r, c_url)
        except BaseException as e:
            warning_issuer.issue_warning_from_exception(e, c_url.get_url())
            self.schedule.report_found_urls([])
        self._update_crawl_delay()
        self.delay_sw.start()
        return True

    def finish(self):
        self.output.end()

    def _parse_page(self, response, c_url):
        """ Parses the page and sends information to output. Process include (according to configuration)
            - Gathering emails, phone numbers, social media, custom_regex
            - Scheduling newly discovered links

            response is a response object generated by requests library
            c_url is a CrawlerURL object
        """
        scraper = Scraper(response.text, "html.parser")
        url_parts = c_url.get_url_parts()
        if self.config.scrape_email:
            for email in scraper.find_all_emails():
                self.output.add_email(email)
        if self.config.scrape_phone_number:
            for number in scraper.find_all_phones():
                self.output.add_phone(number)
        if self.config.scrape_social_media:
            for social in scraper.find_all_social():
                self.output.add_social(social)
        if self.config.custom_regex:
            for regex in scraper.find_all_regex(self.config.custom_regex):
                self.output.add_custom_regex(regex)
        found_c_urls = []
        if self.config.scrape_links:
            for page in scraper.find_all_http_refs():
                page = page.strip().replace(" ", "%20")
                url = url_functions.join_url(c_url.get_url(), page)
                found_c_urls.append(CrawlerURL(url, allow_query=self.config.allow_query))
        self.schedule.report_found_urls(found_c_urls)

        page_info = {"path": c_url.get_extension(),
                     "title": scraper.title.string if scraper.title and scraper.title.string else url_parts.path.split("/")[-1],
                     "custom_string_occurances": scraper.string_occurances(self.config.custom_str, self.config.custom_str_case_sensitive) if self.config.custom_str else None,
                     "on_fuzz_list": c_url.is_fuzzed(),
                     "on_robots": c_url.in_robots(),
                     "code": response.status_code}
        self.output.add_page(c_url.get_netloc(), page_info)

    def _parse_document(self, response, c_url):
        parser = responseparser.DocumentResponse(response, self.config.documents)
        data = parser.extract()
        self.schedule.report_found_urls([])
        if data:
            data["path"] = c_url.get_url_parts().path
            self.output.add_document(c_url.get_netloc(), data)

    def _update_crawl_delay(self):
        default_delay = random.choice(self.config.default_delay)
        s_delay = self.schedule.get_crawl_delay()
        self.delay_sw = Timer(default_delay if default_delay > s_delay else s_delay)

    def dumps(self, **kwargs):
        return self.output.dumps(**kwargs)
