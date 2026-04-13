#!/usr/bin/env python

import argparse
import asyncio
import datetime
import difflib
from functools import total_ordering
import json
import logging
from operator import itemgetter
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import sys
import traceback
from urllib.parse import urlparse
import zipfile

from bs4 import BeautifulSoup
from pysteamauth.auth import Steam
from pysteamauth.base import BaseCookieStorage

version_regex = re.compile(r'^(\d+)\.(\d+)([a-z]?)$')

@total_ordering
class Release:

	def __init__(self, row):
		cells = row.find_all('td')
		for span in cells[1].find_all('span'):
			span.decompose()
		link_tag = cells[2].find('a')
		self.date = cells[0].get_text(strip=True)
		self.version = cells[1].get_text(strip=True)
		self.link = link_tag['href'] if link_tag else None
		self.notes = cells[3].get_text(strip=True)
		major, minor, patch = version_regex.search(self.version).groups()
		self.version_tuple = (int(major), int(minor), patch)

	def __eq__(self, other):
		return isinstance(other, Release) and self.version_tuple == other.version_tuple

	def __lt__(self, other):
		return self.version_tuple < other.version_tuple

logger = logging.getLogger(__name__)

async def main():

	parser = argparse.ArgumentParser(
		description='Generate Git repo from Steamworks SDK releases',
		epilog='''
You must have a Steam account with Steam Guard enabled and provide credentials through env vars:
  STEAM_LOGIN           your Steam account username
  STEAM_PASSWORD        your Steam account password
  STEAM_SHARED_SECRET   your Steam Guard shared secret (use e.g. steamguard-cli to get)

These environment variables are recommended to be set for commit hash reproducibility:
  GIT_COMMITTER_NAME
  GIT_COMMITTER_EMAIL

Use the following options to prevent Git global config from affecting commit hashes:
  --git-commit-arg=--author="Valve <questions@valvesoftware.com>"
  --git-commit-arg=--no-gpg-sign
  --git-commit-arg=--no-post-rewrite
  --git-commit-arg=--no-verify
''',
		formatter_class=argparse.RawDescriptionHelpFormatter
	)
	parser.add_argument('repo', help='path to the Git repo')
	parser.add_argument('--cookies', default='cookies.json', help='path to cookies store; format: nested JSON, root[username][domain][key] = value')
	parser.add_argument('--download', default='download', help='path to the directory to store releases of Steamworks SDK')
	parser.add_argument('--redownload', action='store_true', help='whether to force downloading existing releases')
	parser.add_argument('--nuke', action='store_true', help='delete the repo before starting')
	parser.add_argument('--git-commit-arg', default=[], action='append', help='additional command line arguments for `git commit`; may specify multiple times')
	parser.add_argument('--git-tag-arg', default=[], action='append', help='additional command line arguments for `git tag`; may specify multiple times')
	parser.add_argument('--git-exe', default='git', help='path to git command executable')
	parser.add_argument('--git-branch', default='sdk', help='the git branch to work on')
	parser.add_argument('--log-level', default='info', choices=['debug', 'info', 'warning', 'error', 'critical'], help='log level')
	args = parser.parse_args()

	logging.basicConfig(stream=sys.stderr, level=getattr(logging, args.log_level.upper()))
	logger.debug(f'Command line arguments: {repr(args)}')

	login = os.environ.get('STEAM_LOGIN')
	password = os.environ.get('STEAM_PASSWORD')
	shared_secret = os.environ.get('STEAM_SHARED_SECRET')
	if not login or not password or not shared_secret:
		logger.error('Please provide the environment variables STEAM_LOGIN, STEAM_PASSWORD, and STEAM_SHARED_SECRET')
		sys.exit(1)

	output_path = Path(os.environ.get('GITHUB_OUTPUT', '.out'))
	logger.debug(f'Writing output to {output_path}')
	try:
		output_file = output_path.open('w')
	except Exception:
		logger.warning(f'Failed to open output file {output_path}')
		logger.warning(traceback.format_exc())
		output_file = None
	def output(name, value):
		if not output_file:
			return
		if '\n' not in value:
			print(f'{name}={value}', file=output_file)
			return
		delimiter = 'CHIMICHERRYCHANGA'
		while delimiter in value:
			delimiter += '_PICKLE_BARREL_KUMQUAT'
		print(f'{name}<<{delimiter}', file=output_file)
		print(value.removesuffix('\n'), file=output_file)
		print(delimiter, file=output_file)
	try:
		output('started', '1')
	except Exception:
		logger.warning(f'Failed to write output to {output_path}')
		logger.warning(traceback.format_exc())
		output_file = None

	old_commits = None
	async def clean_up():
		if cookies_path:
			logger.info('Writing cookies')
			try:
				with cookies_path.open('w') as f:
					json.dump(cookie_storage.cookies, f)
			except Exception:
				logger.warning('Failed to write cookies')
				logger.warning(traceback.format_exc())
		try:
			await steam.close()
		except Exception:
			pass
		if old_commits is not None:
			try:
				new_commits = git('rev-list', 'HEAD').splitlines()
				diff = list(map(lambda line: line + '\n', difflib.unified_diff(old_commits, new_commits, lineterm='')))
				summary_path = Path(os.environ.get('GITHUB_STEP_SUMMARY', '.summary'))
				logger.debug(f'Writing summary to {summary_path}')
				with summary_path.open('w') as summary_file:
					if diff:
						print('```diff', file=summary_file)
						summary_file.writelines(diff)
						print('```', file=summary_file)
					else:
						print('Diff is empty.', file=summary_file)
				output('diff', ''.join(diff))
			except Exception:
				logger.error(f'Failed to write summary to {summary_path}')
				logger.error(traceback.format_exc())
		try:
			output_file.close()
		except Exception:
			pass

	def git(*git_args, env=None, check=True):
		git_args = (args.git_exe, '-C', args.repo, '--no-pager', '-c', 'color.ui=false', '-c', 'core.autocrlf=false', *git_args)
		logger.debug(f'Running git command: {repr(git_args)}')
		result = subprocess.run(git_args, env=env, capture_output=True)
		if result.stderr:
			if check:
				logger.warning(f'Git command {repr(git_args)} printed to stderr: {result.stderr.decode('utf-8').strip()}')
			else:
				logger.debug(result.stderr.decode('utf-8').strip())
		if result.returncode and check:
			raise Exception(f'Git command {repr(git_args)} returned nonzero code {result.returncode}')
		return result.stdout.decode('utf-8') if check else not result.returncode

	cookie_storage = BaseCookieStorage()
	cookies_path = Path(args.cookies)
	try:
		if cookies_path.exists():
			if cookies_path.is_dir():
				logger.error(f'Cookies path {args.cookies} is a dir instead of a file')
				cookies_path = None
			else:
				logger.info('Reading existing cookies')
				with cookies_path.open() as f:
					cookie_storage.cookies = json.load(f)
	except Exception:
		logger.warning('Failed to read cookies')
		logger.warning(traceback.format_exc())
		cookies_path = None

	steam = Steam(
		login=login,
		password=password,
		shared_secret=shared_secret,
		cookie_storage=cookie_storage
	)
	logger.info('Logging in')
	try:
		await steam.login_to_steamworks()
	except Exception:
		logger.error('Failed to log in Steamworks')
		logger.error(traceback.format_exc())
		await clean_up()
		sys.exit(1)

	try:
		downloads_list_response = await steam.request_raw(url='https://partner.steamgames.com/downloads/list')
	except Exception:
		logger.error('Failed to get downloads list of Steamworks API')
		logger.error(traceback.format_exc())
		await clean_up()
		sys.exit(1)
	if downloads_list_response.status != 200:
		logger.error('Failed to retrieve downloads list at https://partner.steamgames.com/downloads/list:')
		logger.error(f'The response status is {downloads_list_response.status} instead of 200; did you provide valid credentials?')
		await clean_up()
		sys.exit(1)
	downloads_list_table = BeautifulSoup(await downloads_list_response.text(), 'html.parser').find('table', class_='releaseTable')

	downloads_list = list(map(Release, downloads_list_table.find_all('tr')[1:]))
	downloads_list.sort()
	logger.info(f'Retrieved downloads list with {len(downloads_list)} items, latest version being {downloads_list[-1].version}')

	last_message = ''
	redacted_count = 0
	for item in downloads_list:
		last_message = f'v{item.version}: {item.notes.replace('\n', ' ')}\n{last_message}'
		if item.link:
			item.message = last_message
			last_message = ''
		else:
			item.message = None
			redacted_count += 1
	logger.info(f'There are {redacted_count} redacted releases in total')
	if redacted_count == len(downloads_list):
		logger.error(f'There are no downloads to work with')
		await clean_up()
		sys.exit(1)

	repo = Path(args.repo)
	try:
		if repo.exists() and args.nuke:
			logger.debug('Deleting existing repo')
			shutil.rmtree(repo)
		if not repo.joinpath('.git').is_dir():
			logger.debug('Initializing git repo')
			repo.mkdir(parents=True, exist_ok=True)
			git('init', '--initial-branch', args.git_branch)
	except Exception:
		logger.error('Failed to initialize git repo')
		logger.error(traceback.format_exc())
		await clean_up()
		sys.exit(1)
	old_commits = git('rev-list', 'HEAD').splitlines() if git('rev-parse', 'HEAD', check=False) else []

	def reset_to_before(commit_hash):
		output('reset', '1')
		_, *parents = git('rev-list', '--parents', '--max-count', '1', commit_hash).split()
		if parents:
			git('reset', '--hard', parents[0])
			return
		temp_branch = 'chimicherrychanga'
		while git('show-ref', '--verify', '--quiet', f'refs/heads/{temp_branch}', check=False):
			temp_branch += '-pickle-barrel-kumquat'
		git('checkout', '--orphan', temp_branch)
		git('rm', '-rf', '.', '--quiet', check=False)
		git('branch', '--force', '--delete', args.git_branch)
		git('branch', '--move', args.git_branch)

	index = 0
	if git('rev-parse', 'HEAD', check=False):
		logger.info('Making the repo pristine')
		git('reset', '--hard', 'HEAD', check=False)
		git('clean', '-d', '--force', '-x')
		git('merge', '--abort', check=False)
		git('rebase', '--abort', check=False)
		git('cherry-pick', '--abort', check=False)
		git('revert', '--abort', check=False)
		git('bisect', 'reset', check=False)
		git('am', '--abort', check=False)
		git('checkout', '-B', args.git_branch)

		first_merge = git('log', '--reverse', '--merges', '--format=%H', '--max-count', '1').strip()
		if first_merge:
			logger.info('The commit history is not linear; resetting to the first non-merge commit')
			reset_to_before(first_merge)

		for commit_hash in git('log', '--reverse', '--format=%H').splitlines():
			while index < len(downloads_list) and not downloads_list[index].message:
				index += 1
			if index >= len(downloads_list):
				logger.info('Deleting excessive commits')
				reset_to_before(commit_hash)
				output('reset', '1')
				break
			actual_message = git('show', '-s', '--format=%B', commit_hash).removesuffix('\n')
			if actual_message != downloads_list[index].message:
				logger.debug(f'Expected commit message: {repr(downloads_list[index].message)}')
				logger.debug(f'Actual commit message: {repr(actual_message)}')
				logger.info(f'{commit_hash} is the first bad commit')
				reset_to_before(commit_hash)
				break
			index += 1
	elif git('branch', '--show-current').strip() != args.git_branch:
		git('branch', '--force', '--delete', args.git_branch)
		git('checkout', '-b', args.git_branch)

	try:
		download = Path(args.download)
		if download.is_file():
			download.unlink()
		download.mkdir(parents=True, exist_ok=True)
	except Exception:
		logger.error('Failed to prepare download dir')
		logger.error(traceback.format_exc())
		await clean_up()
		sys.exit(1)

	commit_env = os.environ.copy()
	new_commit_count = 0
	for item in downloads_list[index:]:
		if not item.message:
			logger.debug(f'Skipping v{item.version} because it is redacted')
			continue

		try:
			download_path = download.joinpath(posixpath.basename(urlparse(item.link).path))
			if download_path.is_dir():
				shutil.rmtree(download_path)
			elif download_path.exists() and not args.redownload:
				logger.debug(f'Skipping downloading v{item.version} because it has been downloaded before')
			else:
				logger.info(f'Downloading v{item.version} at {item.link}')
				response = await steam.request_raw(item.link, allow_redirects=False)
				if response.status != 200:
					logger.error(f'Failed to download {item.link}:')
					logger.error(f'The response status is {response.status} instead of 200')
					await clean_up()
					sys.exit(1)
				with download_path.open('wb') as f:
					async for chunk in response.content.iter_chunked(65536):
						f.write(chunk)
		except Exception:
			logger.error(f'Failed to get v{item.version}')
			logger.error(traceback.format_exc())
			await clean_up()
			sys.exit(1)

		logger.debug('Deleting all files in the repo')
		git('rm', '-rf', '.', '--quiet', check=False)

		logger.debug(f'Unzipping v{item.version} release to the repo')
		try:
			with zipfile.ZipFile(download_path, 'r') as zip_ref:
				warned_non_sdk = False
				for zipinfo in zip_ref.infolist():
					path_parts = Path(zipinfo.filename).parts
					if path_parts[0] != 'sdk':
						if not warned_non_sdk:
							logger.warning(f'Zip of v{item.version} does not only have the `sdk` dir at top level')
							warned_non_sdk = True
						continue
					extracted_path = repo.joinpath(*path_parts[1:])
					if zipinfo.is_dir():
						extracted_path.mkdir(parents=True, exist_ok=True)
					else:
						extracted_path.parent.mkdir(parents=True, exist_ok=True)
						with zip_ref.open(zipinfo) as source, extracted_path.open('wb') as dest:
							dest.write(source.read())
					#permission = zipinfo.external_attr >> 16
					#if permission == 0:
					#	permission = 0o644
					#extracted_path.chmod(permission)
				latest_entry = max(zip_ref.infolist(), key=lambda zipinfo: zipinfo.date_time)
		except Exception:
			logger.error(f'Failed to unzip v{item.version} to {repo}')
			logger.error(traceback.format_exc())
			await clean_up()
			sys.exit(1)
		date_iso = datetime.datetime(*latest_entry.date_time, tzinfo=datetime.timezone.utc).isoformat()
		logger.info(f'Source date is set to {date_iso} (that of {latest_entry.filename})')

		commit_env['GIT_COMMITTER_DATE'] = date_iso
		git('add', '--all')
		git(
			'commit',
			'--message', item.message,
			'--date', date_iso,
			'--cleanup', 'verbatim',
			*args.git_commit_arg,
			env=commit_env,
		)
		git('tag', f'v{item.version}', '--force', *args.git_tag_arg)
		new_commit_count += 1
		commit_hash = git('rev-parse', 'HEAD').strip()
		logger.info(f'Commit v{item.version} finished ({commit_hash})')
	output('new-commit-count', str(new_commit_count))
	output('has-new', '1' if new_commit_count else '0')
	output('commit', commit_hash)
	output('commit-short', commit_hash[:7])

	logger.info('All done; closing')
	await clean_up()

if __name__ == '__main__':
	asyncio.run(main())
