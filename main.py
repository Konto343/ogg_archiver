import yt_dlp
from termcolor import cprint
import json
import os
import requests
from PIL import Image
from mutagen import File
from concurrent.futures import ThreadPoolExecutor
import db

# settings
refresh_cache_at_scan = False # force update only PLAYLIST and CHANNEL cache
update_metadata_existing = False # force update file metadata with cache, if file already exists
silent = True
thread_pool = ThreadPoolExecutor(max_workers=1)
dry_run = False

# ratelimits (important!)
sleep_downloading = (60, 120, 3)
sleep_info = (10, 20)
download_rate_limit = 500000

# init
output_dir = '/run/media/user/MAIN/media/music/youtube'
os.makedirs(output_dir, exist_ok=True)
archive = []

class album_song():
	def __init__(self):
		self.title = None
		self.index = 1
		self.url = None
		self.album_title = None
		self.album_thumbnail = None
		self.album_url = None # for re-caching if error
		self.channel = None
		self.id = None
		self.year = None

def strip_producers(input):
	return input.replace(' - Topic','') \
		.replace(' Official', '') \
		.replace('Official', '') \
		.replace('official', '')

def clean_str(input):
	input = input \
		.lower() \
		.replace('/','_') \
		.replace('⧸','_') \
		.replace(' - topic','') \
		.replace(' official', '') \
		.replace(' ', '_')
	
	if input[-1] == ' ':
		input = input[:-1]

	reserved = ['<', '>', ':', '"', '/', '\\', '|', '?', '*', '.']
	for char in reserved:
		if char in input:
			input = input.replace(char, '')
		
	return input

def add_archive(url):
	archive.append(url)
	with open('archive.txt', 'a+') as f:
		f.write(f'{url}\n')
	cprint(f'Archived: {url}', 'green')

def crop_image_square(image_path):
	img = Image.open(image_path)

	width, height = img.size

	if width == height:
		return

	new_size = min(width, height)

	left = (width - new_size) / 2
	top = (height - new_size) / 2
	right = (width + new_size) / 2
	bottom = (height + new_size) / 2

	img_cropped = img.crop((left, top, right, bottom))
	img_cropped.save(image_path)

def get_id(url):
	if url_type == 'channel' and '@' in url:
		return url.split('@')[1].split('/')[0]
	elif url_type == 'channel_alt':
		return url.split('/')[4]
	else:
		return url.split('?')[-1].split('=')[-1]

def get_info(url, force_update=False) -> dict | bool:
	url_type = get_link_type(url)
	url_id = get_id(url)

	if not url_type:
		cprint(f'Invaild Url Type: {url}', 'red')
		return False

	if not url_type:
		cprint(f'Invaild Url Id: {url}', 'red')
		return False

	cache_entry = db.get_entry(url_type, url_id)

	if cache_entry and not force_update:
		return json.loads(cache_entry[0])

	if force_update:
		cprint(f'Forcing update cache on: {url}', 'yellow')

	cprint(f'Processing Info: {url}', 'cyan')

	try:
		ydl_opts = {
			'quiet' : silent,
			'noprogress' : silent,
			'no_warnings' : silent,

			'sleep_interval' : sleep_info[0],
			'max_sleep_interval' : sleep_info[1],

			'extract_flat': True,
			'force_generic_extractor': True,
		}

		with yt_dlp.YoutubeDL(ydl_opts) as ydl:
			info = ydl.extract_info(url, download=False)

			if force_update:
				db.update_entry(url_type, url_id, json.dumps(info))
			else:
				db.add_entry(url_type, url_id, json.dumps(info))
			return info
	except Exception as e:
		cprint(f'Info error: {e}', 'red')
		add_archive(url)
		return False

def get_link_type(url):
	if 'youtube.com/@' in url:
		return 'channel'
	elif 'youtube.com/channel' in url:
		return 'channel_alt'
	elif 'youtube.com/playlist?list' in url:
		return 'playlist'
	elif 'youtube.com/watch?v=' in url:
		return 'video'
	
	print('Bad entry! No type declared!', url)
	return None

def get_list(file):
	return [line.strip() for line in open(file, 'r').readlines()]

def download(url, file_path) -> bool:
	if os.path.exists(file_path):
		return True

	try:
		response = requests.get(url, stream=True)
		response.raise_for_status()

		with open(file_path, 'wb') as f:
			for chunk in response.iter_content(1024):
				f.write(chunk)
		return True
	except Exception as e:
		cprint(f'Download Error => {e}','red')
		return False

def download_video(url, path) -> bool:
	ydl_opts = {
		'quiet' : silent,
		'noprogress' : silent,
		'no_warnings' : silent,

		#'cookiefile' : 'cookies.txt',

		'format': "bestaudio[ext=opus]/bestaudio",
		'outtmpl': f'{path}.%(ext)s',

		'ratelimit' : download_rate_limit,
		'sleep_interval_requests' : sleep_downloading[2],
		'sleep_interval' : sleep_downloading[0],
		'max_sleep_interval' : sleep_downloading[1],

		'final_ext': 'ogg',
		'postprocessors': [{
			'key': 'FFmpegVideoRemuxer',
			'preferedformat': 'ogg',
		}],
	}

	try:
		with yt_dlp.YoutubeDL(ydl_opts) as ydl:
			ydl.download([url])
			return True
	except Exception as e:
		cprint(f'Download error => {e}', 'red')
		add_archive(url)
		return False

def add_metadata(file_path, song : album_song):
	audio = File(file_path)

	if not audio:
		return

	audio['TRACKNUMBER'] = str(song.index + 1)
	audio['ARTIST'] = song.channel.replace(' - Topic', '').replace(' Official', '').replace('official', '').replace('Official','')
	audio['ALBUM'] = song.album_title
	audio['TITLE'] = song.title # for more reliable FULL title

	if song.year != None:
		audio['DATE'] = str(song.year)
		
	audio.save()

def get_songs(url) -> [album_song]:
	songs = []

	link_type = get_link_type(url)
	data = get_info(url, force_update=refresh_cache_at_scan)

	if not data or not 'channel' in data:
		cprint(f'No data on: {url}!', 'red')
		return songs

	# prep channel folder / download artist thumbnails
	channel_dir = os.path.join(output_dir, clean_str(data['channel']))
	os.makedirs(channel_dir, exist_ok=True)
	for thumbnail in data['thumbnails']:
		if thumbnail['id'] == "avatar_uncropped" or thumbnail['id'] == 'banner_uncropped':
			thumbnail_type = "artist" if thumbnail['id'] == 'avatar_uncropped' else 'backdrop'
			download_path = channel_dir + f'/{thumbnail_type}.jpg'
			download(thumbnail['url'], download_path)

	# phrase url and make songs (Albums)
	if link_type == 'channel':
		# @channel/releases:
		# 	playlists[name] -> videos[minimal]
		if '/releases' in url:
			for index, entry in enumerate(data['entries']):
				if entry['url'] in archive:
					continue

				playlist = get_info(entry['url'], force_update=refresh_cache_at_scan)

				if not playlist:
					cprint('Playlist invaild!', 'red')
					continue

				# first_video = playlist['entries'][0]['url']

				# if first_video in archive:
				# 	cprint(f'First video is archived => {first_video['title']}', 'red')
				# 	continue

				# first_entry = get_info(first_video)

				# if not first_entry:
				# 	cprint('First entry is invaild!', 'red')
				# 	continue

				for playlist_index, video in enumerate(playlist['entries']): 
					if video['url'] in archive:
						cprint(f'Video is archived => {video['title']}', 'red')
						continue

					video_data = get_info(video['url'])

					if not video_data:
						cprint('Video bad data!', 'red')
						continue

					song = album_song()
					song.title = video_data['title']
					song.index = playlist_index
					song.url = video['url']
					song.album_title = video_data['album'] if 'album' in video_data else playlist['title']
					song.album_thumbnail = playlist['thumbnails'][-2] # -2 for max res.
					
					# old, because album thumbnails fall back on first entry anyways, no need for local vaildation
					# song.album_thumbnail = first_entry['thumbnails'][-2] # -2 for JPEG and not WEBP
					song.album_url = entry['url']
					song.channel = data['channel']
					song.id = video_data['id']
					song.year = video_data['release_year']
					songs.append(song)

		# @channel/videos:
		# 	videos[minimal]
		if '/videos' in url:
			for index, entry in enumerate(data['entries']):
				if entry['url'] in archive:
					cprint('Video is archived!', 'red')
					continue

				video_data = get_info(entry['url'])

				if not video_data:
					cprint('Video bad data!', 'red')
					continue
	
				song = album_song()
				song.title = video_data['title']
				song.index = index
				song.url = entry['url']
				song.album_title = video_data['album'] if 'album' in video_data else '_unknown'
				song.album_thumbnail = video_data['thumbnails'][-2] # -2 for JPEG and not WEBP
				song.album_url = entry['url']
				song.channel = data['channel']
				song.id = video_data['id']
				song.year = video_data['upload_date'][:4]
				songs.append(song)


	# @topic channel:
	# 	videos[minimal] OR playlists[videos[full]]
	if link_type == 'channel_alt':
		for index, entry in enumerate(data['entries']):
			if entry['ie_key'] == 'YoutubeTab':
				playlist = get_info(entry['url'])
				for index, entry in enumerate(playlist['entries']):
					if entry['url'] in archive:
						cprint('Video is archived!', 'red')
						continue

					video_data = get_info(entry['url'])

					if not video_data:
						cprint('Video bad data!', 'red')
						continue
			
					song = album_song()
					song.title = video_data['title']
					song.index = index
					song.url = entry['url']
					song.album_title = video_data['album'] if 'album' in video_data else '_unknown'
					song.album_thumbnail = video_data['thumbnails'][-2] # -2 for JPEG and not WEBP
					song.album_url = entry['url']
					song.channel = data['channel']
					song.id = video_data['id']
					song.year = video_data['upload_date'][:4]
					songs.append(song)

			if entry['ie_key'] == 'Youtube':
				if entry['url'] in archive:
					cprint('Video is archived!', 'red')
					continue

				video_data = get_info(entry['url'])

				if not video_data:
					cprint('Video bad data!', 'red')
					continue
		
				song = album_song()
				song.title = video_data['title']
				#song.index = index
				# indexing is not reliable if the topic channel has a huge list of ONLY videos
				song.url = entry['url']
				song.album_title = video_data['album'] if 'album' in video_data else '_unknown'
				song.album_thumbnail = video_data['thumbnails'][-2] # -2 for JPEG and not WEBP
				song.album_url = entry['url']
				song.channel = data['channel']
				song.id = video_data['id']
				song.year = video_data['upload_date'][:4]
				songs.append(song)

	cprint(f'Collected {len(songs)} songs', 'green')

	return songs

def download_song(song : album_song):
	global archive, output_dir

	if song.url in archive:
		return

	song.channel = strip_producers(song.channel)

	song_channel = clean_str(song.channel)
	song_album = clean_str(song.album_title)
	song_title = clean_str(song.title)
	song_id = song.id
	song_url = song.url

	song_dir = os.path.join(output_dir, song_channel, song_album)
	song_path = os.path.join(song_dir, song_id + '.ogg')
	song_path_noext = os.path.join(song_dir, song_id) # used for downloading the song without it's extension, dumb i know
	song_cover = os.path.join(song_dir, 'cover.jpg')

	os.makedirs(song_dir, exist_ok=True)

	if dry_run:
		return

	try:
		if not os.path.exists(song_path):
			cprint(f'Downloading: [{song_channel}] {song_title}', 'light_green')
			success = download_video(song_url, song_path_noext)

			if success:
				add_metadata(song_path, song)
				cprint(f'Downloaded: [{song_channel}] {song_title}', 'green')
		else:
			cprint(f'Already Exists: [{song_channel}] {song_title}', 'yellow')
			if update_metadata_existing:
				add_metadata(song_path, song)

		if not os.path.exists(song_cover):
			cprint('Cover is missing... downloading...', 'yellow')

			if song.album_thumbnail != '':
				success = download(song.album_thumbnail['url'], song_cover)

				if success:
					crop_image_square(song_cover)
					return

				cprint('Cover failed. Redownloading info...', 'yellow')

				new_data = get_info(song.album_url, force_update=True)
				new_thumbnail = new_data['thumbnails'][-2]['url']
				state = download(new_thumbnail, song_cover)

				if state:
					cprint("Downloaded Album Cover!", 'green')
					crop_image_square(song_cover)
				else:
					cprint('Album cover failed to download!', 'red')
			else:
				cprint("Album URL missing!", 'red')
	except Exception as e:
		cprint(e, 'red')

def main():
	global archive, output_dir

	targets = get_list('list.txt')
	archive.extend(get_list('archive.txt'))

	# prep metadata and get songs
	for target in targets:
		if target == '':
			continue

		if target[0] == '#':
			continue

		cprint(f'Fetching songs on: {target}', 'cyan')
		songs = get_songs(target)
	
		for index, song in enumerate(songs):
			cprint(f'[{index+1}/{len(songs)}]', 'magenta')
			download_song(song)

if __name__ == '__main__':
	db.init()
	main()
