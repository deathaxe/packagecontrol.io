import re
from urllib.parse import quote

from ..versions import version_sort, version_process
from .json_api_client import JSONApiClient
from ..downloaders.downloader_exception import DownloaderException


# A predefined list of readme filenames to look for
_readme_filenames = [
    'readme',
    'readme.txt',
    'readme.md',
    'readme.mkd',
    'readme.mdown',
    'readme.markdown',
    'readme.textile',
    'readme.creole',
    'readme.rst'
]


class BitBucketClient(JSONApiClient):

    @staticmethod
    def make_repo_url(owner_name, repo_name):
        """
        Generate the tags URL for a GitHub repo if the value passed is a GitHub
        repository URL

        :param owener_name:
            The repository owner name

        :param repo_name:
            The repository name

        :return:
            The repositoy URL of given owner and repo name
        """

        return 'https://bitbucket.com/%s/%s' % (quote(owner_name), quote(repo_name))

    @staticmethod
    def make_tags_url(repo_url):
        """
        Generate the tags URL for a BitBucket repo if the value passed is a BitBucket
        repository URL

        :param repo_url:
            The repository URL

        :return:
            The tags URL if repo_url was a BitBucket repo, otherwise False
        """

        match = re.match('https?://bitbucket.org/([^/]+/[^/]+)/?$', repo_url)
        if not match:
            return False

        return 'https://bitbucket.org/%s#tags' % match.group(1)

    @staticmethod
    def make_branch_url(repo_url, branch):
        """
        Generate the branch URL for a BitBucket repo if the value passed is a BitBucket
        repository URL

        :param repo_url:
            The repository URL

        :param branch:
            The branch name

        :return:
            The branch URL if repo_url was a BitBucket repo, otherwise False
        """

        match = re.match('https?://bitbucket.org/([^/]+/[^/]+)/?$', repo_url)
        if not match:
            return False

        return 'https://bitbucket.org/%s/src/%s' % (match.group(1), quote(branch))

    def download_info(self, url, tag_prefix=None):
        """
        Retrieve information about downloading a package

        :param url:
            The URL of the repository, in one of the forms:
              https://bitbucket.org/{user}/{repo}
              https://bitbucket.org/{user}/{repo}/src/{branch}
              https://bitbucket.org/{user}/{repo}/#tags
            If the last option, grabs the info from the newest
            tag that is a valid semver version.

        :param tag_prefix:
            If the URL is a tags URL, only match tags that have this prefix.
            If tag_prefix is None, match only tags without prefix.

        :raises:
            DownloaderException: when there is an error downloading
            ClientException: when there is an error parsing the response

        :return:
            None if no match, False if no commit, or a list of dicts with the
            following keys:
              `version` - the version number of the download
              `url` - the download URL of a zip file of the package
              `date` - the ISO-8601 timestamp string when the version was published
        """

        output = []

        version = None
        url_pattern = 'https://bitbucket.org/%s/get/%s.zip'

        # tag based releases
        tags_match = re.match('https?://bitbucket.org/([^/]+/[^#/]+)/?#tags$', url)
        if tags_match:
            user_repo = tags_match.group(1)

            tags_list = {}
            tags_url = self._make_api_url(user_repo, '/refs/tags?pagelen=100')
            while tags_url:
                tags_json = self.fetch_json(tags_url)
                for tag in tags_json['values']:
                    tags_list[tag['name']] = tag['target']['date'][0:19].replace('T', ' ')
                tags_url = tags_json['next'] if 'next' in tags_json else None

            tag_info = version_process(tags_list.keys(), tag_prefix)
            tag_info = version_sort(tag_info, reverse=True)
            if not tag_info:
                return False

            max_releases = self.settings.get('max_releases', 0)

            used_versions = set()
            for info in tag_info:
                version = info['version']
                if version in used_versions:
                    continue

                tag = info['prefix'] + version
                output.append({
                    'url': url_pattern % (user_repo, tag),
                    'version': version,
                    'date': tags_list[tag]
                })
                used_versions.add(version)
                if max_releases > 0 and len(used_versions) >= max_releases:
                    break

        # branch based releases
        else:
            user_repo, branch = self._user_repo_branch(url)
            if not user_repo:
                return None

            if branch is None:
                repo_info = self.fetch_json(self._make_api_url(user_repo))
                branch = repo_info['mainbranch'].get('name', 'master')

            branch_url = self._make_api_url(user_repo, '/refs/branches/%s' % branch)
            branch_info = self.fetch_json(branch_url)

            timestamp = branch_info['target']['date'][0:19].replace('T', ' ')

            output = [{
                'url': url_pattern % (user_repo, branch),
                'version': re.sub(r'[\-: ]', '.', timestamp),
                'date': timestamp
            }]

        return output

    def repo_info(self, url):
        """
        Retrieve general information about a repository

        :param url:
            The URL to the repository, in one of the forms:
              https://bitbucket.org/{user}/{repo}
              https://bitbucket.org/{user}/{repo}/src/{branch}

        :raises:
            DownloaderException: when there is an error downloading
            ClientException: when there is an error parsing the response

        :return:
            None if no match, or a dict with the following keys:
              `name`
              `description`
              `homepage` - URL of the homepage
              `author`
              `readme` - URL of the readme
              `issues` - URL of bug tracker
              `donate` - URL of a donate page
        """

        user_repo, branch = self._user_repo_branch(url)
        if not user_repo:
            return user_repo

        api_url = self._make_api_url(user_repo)
        repo_info = self.fetch_json(api_url)

        if branch is None:
            branch = repo_info['mainbranch'].get('name', 'master')

        issues_url = 'https://bitbucket.org/%s/issues' % user_repo

        author = repo_info['owner'].get('nickname')
        if author is None:
            author = repo_info['owner'].get('username')

        return {
            'name': repo_info['name'],
            'description': repo_info['description'] or 'No description provided',
            'homepage': repo_info['website'] or url,
            'author': author,
            'donate': None,
            'readme': self._readme_url(user_repo, branch),
            'issues': issues_url if repo_info['has_issues'] else None
        }

    def user_info(self, url):
        """
        For API compatibility with other clients.

        :param url:
            The URL to the repository, in one of the forms:
              https://bitbucket.org/{user}

        :return:
            None
        """
        return None

    def _main_branch_name(self, user_repo):
        """
        Fetch the name of the default branch

        :param user_repo:
            The user/repo name to get the main branch for

        :raises:
            DownloaderException: when there is an error downloading
            ClientException: when there is an error parsing the response

        :return:
            The name of the main branch - `master` or `default`
        """

        main_branch_url = self._make_api_url(user_repo)
        main_branch_info = self.fetch_json(main_branch_url, True)
        return main_branch_info['mainbranch']['name']

    def _make_api_url(self, user_repo, suffix=''):
        """
        Generate a URL for the BitBucket API

        :param user_repo:
            The user/repo of the repository

        :param suffix:
            The extra API path info to add to the URL

        :return:
            The API URL
        """

        return 'https://api.bitbucket.org/2.0/repositories/%s%s' % (user_repo, suffix)

    def _readme_url(self, user_repo, branch, prefer_cached=False):
        """
        Parse the root directory listing for the repo and return the URL
        to any file that looks like a readme

        :param user_repo:
            The user/repo string

        :param branch:
            The branch to fetch the readme from

        :param prefer_cached:
            If a cached directory listing should be used instead of a new HTTP request

        :raises:
            DownloaderException: when there is an error downloading
            ClientException: when there is an error parsing the response

        :return:
            The URL to the readme file, or None
        """

        listing_url = self._make_api_url(user_repo, '/src/%s/?pagelen=100' % branch)

        try:
            while listing_url:
                root_dir_info = self.fetch_json(listing_url, prefer_cached)

                for entry in root_dir_info['values']:
                    if entry['path'].lower() in _readme_filenames:
                        return 'https://bitbucket.org/%s/raw/%s/%s' % (user_repo, branch, entry['path'])

                listing_url = root_dir_info['next'] if 'next' in root_dir_info else None

        except (DownloaderException) as e:
            if 'HTTP error 404' not in str(e):
                raise

        return None

    def _user_repo_branch(self, url):
        """
        Extract the username/repo and branch name from the URL

        :param url:
            The URL to extract the info from, in one of the forms:
              https://bitbucket.org/{user}/{repo}
              https://bitbucket.org/{user}/{repo}/src/{branch}

        :raises:
            DownloaderException: when there is an error downloading
            ClientException: when there is an error parsing the response

        :return:
            A tuple of (user/repo, branch name) or (None, None) if not matching
        """

        branch_match = re.match('https?://bitbucket.org/([^/]+/[^/]+)/src/([^/]+)/?$', url)
        if branch_match:
            return branch_match.groups()

        repo_match = re.match('https?://bitbucket.org/([^/]+/[^/]+)/?$', url)
        if repo_match:
            return (repo_match.group(1), None)

        return (None, None)
