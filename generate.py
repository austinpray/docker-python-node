#!/usr/bin/env python3

import itertools
import os
from copy import deepcopy
from glob import glob
from os.path import dirname
from os.path import join
from shutil import unpack_archive
from typing import List
from urllib.request import urlretrieve

import requests
import yaml
from dockerfile_compose import include_dockerfile
from packaging.version import Version


def get_repo_version(repo):
    res = requests.get(f'https://api.github.com/repos/{repo}/branches/master',
                       headers={'Accept': 'application/vnd.github.v3+json'})
    if res.status_code != 200:
        raise RuntimeError(f"Can't get version for {repo}")

    return res.json()['commit']['sha']


repos = {
    'nodejs/docker-node': {
        'version': get_repo_version('nodejs/docker-node')
    },
    'docker-library/python': {
        'version': get_repo_version('docker-library/python')
    }
}


def fetch_all_repos():
    if not os.path.exists('repos'):
        os.makedirs('repos')

    for k, v in repos.items():
        version = v['version']
        url = f'https://github.com/{k}/archive/{version}.zip'
        zip_name = k.split('/')[1]
        zip = f'repos/{zip_name}-{version}.zip'
        urlretrieve(url, zip)
        unpack_archive(zip, extract_dir='repos')


def get_dockerfiles(path):
    return glob(join(path, r'*/stretch/Dockerfile'))


def get_python_dockerfiles():
    return get_dockerfiles('repos/python-{}'.format(repos['docker-library/python']['version']))


def get_node_dockerfiles():
    return get_dockerfiles('repos/docker-node-{}'.format(repos['nodejs/docker-node']['version']))


def update_travis_yaml():
    with open('.travis.yml', 'r') as travis_yaml:
        travis_dict = yaml.safe_load(travis_yaml)

    dockerfiles = glob('dockerfiles/*/Dockerfile')
    travis_dict = travis_yaml_add_stages(travis_dict, dockerfiles)

    with open('.travis.yml', 'w+') as travis_yaml:
        travis_yaml.write('# generated by generate.py\n')
        yaml.safe_dump(travis_dict, travis_yaml, default_flow_style=False)


def get_versions_from_dockerfile(dockerfile_path):
    versions = {'node': None, 'python': None}
    with open(dockerfile_path, 'r') as df:
        for line in df:
            if line.startswith('ENV'):
                name, version = line.split()[1:]
                if name == 'PYTHON_VERSION':
                    versions['python'] = Version(version)
                if name == 'NODE_VERSION':
                    versions['node'] = Version(version)
    return versions


def make_build_stage(dockerfile_path: str, tags: List[str]) -> dict:
    return {
        'stage': 'Image Builds',
        'name': ', '.join(tags),
        'if': 'type NOT IN (cron)',
        'script': [
            'set -e',
            'echo "$DOCKER_PASSWORD" | docker login --username "$DOCKER_USERNAME" --password-stdin',
            '# run tests',
            f'travis_retry docker build -t austinpray/python-node {dirname(dockerfile_path)}',
            *[f'docker tag austinpray/python-node austinpray/python-node:{tag}' for tag in tags],
            *[f'[ "$TRAVIS_BRANCH" = "master" ] && docker push austinpray/python-node:{tag}' for tag in tags]
        ]
    }


def travis_yaml_add_stages(travis_dict: dict, dockerfile_paths: List[str]) -> dict:
    dockerfiles = []
    for dockerfile_path in dockerfile_paths:
        versions = get_versions_from_dockerfile(dockerfile_path)
        dockerfiles.append({
            'dockerfile_path': dockerfile_path,
            'python_version': versions['python'],
            'node_version': versions['node']
        })
    dockerfiles.sort(key=lambda x: (x['python_version'], x['node_version']))
    dockerfiles.reverse()

    def strip_version(version, n=0):
        if n == 0:
            return '.'.join(str(version).split('.'))

        return '.'.join(str(version).split('.')[:n])

    def group_by_version(py_offset=0, node_offset=0):
        group = {}
        for df in deepcopy(dockerfiles):
            key = ''.join([
                strip_version(df['python_version'],
                              py_offset),
                '-',
                strip_version(df['node_version'],
                              node_offset)
            ])
            if key not in group:
                group[key] = df['dockerfile_path']
        return group

    options = [-2, -1, 0]
    dockerfile_tags = {}
    for t in itertools.product(options, options):
        for tag, dockerfile in group_by_version(t[0], t[1]).items():
            if dockerfile not in dockerfile_tags:
                dockerfile_tags[dockerfile] = [tag]
                continue

            dockerfile_tags[dockerfile].append(tag)

    travis_dict['jobs'] = {
        'include': [
            *[make_build_stage(dockerfile_path=df,
                               tags=tags) for df, tags in dockerfile_tags.items()]
        ]
    }
    return travis_dict


def generate_dockerfiles():
    for dockerfileTuple in itertools.product(get_python_dockerfiles(), get_node_dockerfiles()):
        python_version = dockerfileTuple[0].split('/')[2]
        node_version = dockerfileTuple[1].split('/')[2]
        tag = f'{python_version}-{node_version}'
        print(tag)
        tag_dir = f'dockerfiles/{tag}'
        if not os.path.exists(tag_dir):
            os.makedirs(tag_dir)

        with open(join(tag_dir, 'Dockerfile'), 'w+') as template:
            template.write('''
            # This is generated by generate.py, don't edit it directly
            '''.strip())
            template.write('\n')
            template.write('FROM buildpack-deps:stretch\n')
            template.write('\n')
            with open(dockerfileTuple[0], 'r') as df:
                include_dockerfile(df, template)
            with open(dockerfileTuple[1], 'r') as df:
                include_dockerfile(df, template)
            template.write('CMD ["python3"]\n')


def main():
    fetch_all_repos()
    generate_dockerfiles()
    update_travis_yaml()


if __name__ == '__main__':
    main()
