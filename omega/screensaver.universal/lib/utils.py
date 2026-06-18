import hashlib
import os
import json
import re
import sys
import urllib
import xbmc
import xbmcvfs
import xbmcaddon
import xml.etree.ElementTree as etree

ADDON    = xbmcaddon.Addon()
ADDONID = ADDON.getAddonInfo('id')
LANGUAGE = ADDON.getLocalizedString

# supported image types by the screensaver
IMAGE_TYPES = ['.jpg', '.jpeg', '.png', '.tif', '.tiff', '.gif', '.pcx', '.bmp', '.tga', '.ico', '.nef', '.webp', '.jp2', '.apng']
HEIF_TYPES = ['.heic', '.heif']
MPO_TYPES = ['.mpo']
RAW_TYPES = ['.3fr', '.arw', '.cr2', '.crw', '.dcr', '.dng', '.erf', '.kdc', '.mdc', '.mef', '.mos', '.mrw', '.nef', '.nrw', '.orf', '.pef', '.ppm', '.raf', '.raw', '.rw2', '.srw', '.x3f']
CACHEFOLDER = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
CACHEFILE = os.path.join(CACHEFOLDER, 'cache_%s')
RESUMEFILE = os.path.join(CACHEFOLDER, 'offset')
ASFILE = xbmcvfs.translatePath('special://profile/advancedsettings.xml')

def log(txt):
    message = '%s: %s' % (ADDONID, txt)
    xbmc.log(msg=message, level=xbmc.LOGDEBUG)

def checksum(path):
    return hashlib.md5(path).hexdigest()

def create_cache(path, hexfile):
    images = walk(path)
    if not xbmcvfs.exists(CACHEFOLDER):
        xbmcvfs.mkdir(CACHEFOLDER)
    # remove old cache files
    dirs, files = xbmcvfs.listdir(CACHEFOLDER)
    for item in files:
        if item != 'settings.xml':
            xbmcvfs.delete(os.path.join(CACHEFOLDER,item))
    if images:
        # create cache file
        try:
            cache = xbmcvfs.File(CACHEFILE % hexfile, 'w')
            json.dump(images, cache)
            cache.close()
        except:
            log('failed to save cachefile')

def get_excludes():
    regexes = []
    if xbmcvfs.exists(ASFILE):
        try:
            tree = etree.parse(ASFILE)
            root = tree.getroot()
            excludes = root.find('pictureexcludes')
            if excludes is not None:
                for expr in excludes:
                    regexes.append(expr.text)
        except:
            pass
    return regexes

def walk(path):
    images = []
    folders = []
    excludes = get_excludes()
    # multipath support
    if path.startswith('multipath://'):
        # get all paths from the multipath
        paths = path[12:-1].split('/')
        for item in paths:
            folders.append(urllib.unquote_plus(item))
    else:
        folders.append(path)
    for folder in folders:
        if xbmcvfs.exists(xbmcvfs.translatePath(folder)):
            dirs = []
            files = []
            if xbmc.getCondVisibility('System.HasAddon(imagedecoder.heif)'):
                IMAGE_TYPES.extend(HEIF_TYPES)
            if xbmc.getCondVisibility('System.HasAddon(imagedecoder.mpo)'):
                IMAGE_TYPES.extend(MPO_TYPES)
            if xbmc.getCondVisibility('System.HasAddon(imagedecoder.raw)'):
                IMAGE_TYPES.extend(RAW_TYPES)
            # get all files and subfolders
            if folder.startswith('plugin://'):
                getroot = xbmc.executeJSONRPC('{\"jsonrpc\":\"2.0\", \"method\":\"Files.GetDirectory\", \"params\":{\"directory\":\"%s\", \"sort\":{\"method\":\"label\"}}, \"id\":1 }' % folder)
                root = json.loads(getroot)
                if 'result' in root and 'files' in root["result"]:
                    for item in root["result"]["files"]:
                        if item["filetype"] == "file":
                            files.append(item)
                        elif item["filetype"] == "directory":
                            dirs.append(item["file"])
            else:
                dirs, files = xbmcvfs.listdir(folder)
            log('dirs: %s' % len(dirs))
            log('files: %s' % len(files))
            if not folder.startswith('plugin://'):
                # natural sort
                convert = lambda text: int(text) if text.isdigit() else text
                alphanum_key = lambda key: [convert(c) for c in re.split('([0-9]+)', key)]
                files.sort(key=alphanum_key)
            for item in files:
                # check pictureexcludes from as.xml
                fileskip = False
                if excludes:
                    for string in excludes:
                        regex = re.compile(string)
                        if folder.startswith('plugin://'):
                            match = regex.search(item["label"])
                        else:
                            match = regex.search(item)
                        if match:
                            fileskip = True
                            break
                # filter out all images
                if folder.startswith('plugin://'):
                    if os.path.splitext(item["label"])[1].lower() in IMAGE_TYPES and not fileskip:
                        images.append([item["file"], item["label"]])
                else:
                    if os.path.splitext(item)[1].lower() in IMAGE_TYPES and not fileskip:
                        images.append([os.path.join(folder,item), item])
            if xbmcaddon.Addon().getSettingBool('recursive'):
                for item in dirs:
                    # check pictureexcludes from as.xml
                    dirskip = False
                    if excludes:
                        for string in excludes:
                            regex = re.compile(string)
                            match = regex.search(item)
                            if match:
                                dirskip = True
                                break
                    # recursively scan all subfolders
                    if not dirskip:
                        if item.startswith('plugin://'):
                            images += walk(item)
                        else:
                            images += walk(os.path.join(folder,item,'')) # make sure paths end with a slash
        else:
            log('folder does not exist')
    return images


# =============================================================================
# Plugin cache helpers.
# =============================================================================

# Dedicated folder for downloaded plugin images
PLUGIN_IMGFOLDER  = os.path.join(CACHEFOLDER, 'plugin_images')
PLUGIN_FANARTDIR  = os.path.join(PLUGIN_IMGFOLDER, 'fanart')
PLUGIN_LOGODIR    = os.path.join(PLUGIN_IMGFOLDER, 'clearlogo')
PLUGIN_CACHEFILE  = os.path.join(CACHEFOLDER, 'plugin_cache.json')
# Stores {url: local_filename} so we can skip re-downloading unchanged images
PLUGIN_URLMAP     = os.path.join(CACHEFOLDER, 'plugin_url_map.json')

# Goal is 1920x1080 (16:9 fanart, no posters/thumbs)
TARGET_WIDTH  = 1920
TARGET_HEIGHT = 1080
ASPECT_RATIO  = TARGET_WIDTH / float(TARGET_HEIGHT)   # 1.7777...
ASPECT_TOLERANCE = 0.05                               # ±5%

# Kodi startup delay before rebuilding plugin cache
STARTUP_DELAY_SECS = 60


def _ensure_plugin_imgfolder():
    if not xbmcvfs.exists(CACHEFOLDER):
        xbmcvfs.mkdir(CACHEFOLDER)
    if not xbmcvfs.exists(PLUGIN_IMGFOLDER):
        xbmcvfs.mkdir(PLUGIN_IMGFOLDER)
    if not xbmcvfs.exists(PLUGIN_FANARTDIR):
        xbmcvfs.mkdir(PLUGIN_FANARTDIR)
    if not xbmcvfs.exists(PLUGIN_LOGODIR):
        xbmcvfs.mkdir(PLUGIN_LOGODIR)


def _is_hq_16x9(width, height):
    """Return True for 1920 or 3840-wide images with approximately 16:9 aspect ratio."""
    if width not in (1920, 3840):
        return False
    if height == 0:
        return False
    ratio = width / float(height)
    # 16:9 = 1.777. Accept 1.6-1.9 range.
    return 1.6 <= ratio <= 1.9


def _get_image_dimensions(local_path):
    """Read pixel dimensions from a local image file (no PIL needed).
    Streams the file in chunks to handle large JPEG headers (ICC profiles etc).
    """
    try:
        import struct
        with open(local_path, 'rb') as fh:
            # Check PNG first (dimensions always in first 24 bytes)
            header = fh.read(24)
            if header[:8] == b'\x89PNG\r\n\x1a\n':
                w, h = struct.unpack('>II', header[16:24])
                return w, h

            # JPEG: stream through markers until we find SOF
            # Read in large chunks since TMDB images have big ICC/Exif blocks
            fh.seek(0)
            buf = fh.read(4194304)  # read up to 4MB

        i = 0
        while i < len(buf) - 3:
            if buf[i] != 0xFF:
                i += 1
                continue
            marker = buf[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):  # all SOF variants
                if i + 9 <= len(buf):
                    h, w = struct.unpack('>HH', buf[i + 5:i + 9])
                    return w, h
            # Skip markers with no length (standalone)
            if marker in (0xD8, 0xD9, 0xD0, 0xD1, 0xD2, 0xD3,
                          0xD4, 0xD5, 0xD6, 0xD7, 0x01):
                i += 2
                continue
            # Skip segment: FF marker + 2-byte length (length includes itself)
            if i + 3 < len(buf):
                seg_len = struct.unpack('>H', buf[i + 2:i + 4])[0]
                i += 2 + seg_len
            else:
                break
    except Exception as e:
        log('dimension read failed for %s: %s' % (local_path, str(e)))
    return 0, 0


def _url_hash(url):
    """Stable short hash of a source URL, used to name its cached file.
    The same URL always yields the same filename across runs, so images keep a
    consistent on-disk identity and the prune step can reliably match them.
    """
    return hashlib.md5(url.encode('utf-8')).hexdigest()


def _clear_plugin_cache(full=True):
    """Remove cached plugin images and index.
    If full=False, only remove images that are no longer needed (called during smart refresh).
    If full=True (default), wipe everything including the URL map.
    """
    for folder in (PLUGIN_FANARTDIR, PLUGIN_LOGODIR, PLUGIN_IMGFOLDER):
        if xbmcvfs.exists(folder):
            _dirs, files = xbmcvfs.listdir(folder)
            for f in files:
                xbmcvfs.delete(os.path.join(folder, f))
    if xbmcvfs.exists(PLUGIN_CACHEFILE):
        xbmcvfs.delete(PLUGIN_CACHEFILE)
    if full and xbmcvfs.exists(PLUGIN_URLMAP):
        xbmcvfs.delete(PLUGIN_URLMAP)


def _rewrite_to_hq(url):
    """
    Decode Kodi image:// wrapper and rewrite TMDB URLs to /original/ size.
    Appends .jpg to TMDB paths that have no file extension.
    """
    import urllib.parse
    import re

    # Decode Kodi image:// wrapper: image://https%3a%2f%2f.../
    if url.startswith('image://'):
        inner = url[8:]
        if inner.endswith('/'):
            inner = inner[:-1]
        try:
            url = urllib.parse.unquote(inner)
        except Exception:
            pass

    # Rewrite TMDB size segment to 'original'
    tmdb_match = re.match(
        r'(https?://image\.tmdb\.org/t/p/)[^/]+(/.*)', url, re.IGNORECASE)
    if tmdb_match:
        path_part = tmdb_match.group(2)
        # TMDB sometimes omits file extension - append .jpg
        if '.' not in path_part.split('/')[-1]:
            path_part += '.jpg'
        url = tmdb_match.group(1) + 'original' + path_part

    return url

def build_plugin_cache(plugin_path, cache_size, startup_delay=True):
    """
    Crawl plugin_path and cache up to cache_size matched (fanart, clearlogo) pairs.

    Smart refresh: loads the existing URL→filename map so already-downloaded images
    are reused without re-downloading. Only new URLs trigger a download. Images that
    are no longer in the plugin's current listing are deleted from disk.

    full_rebuild (triggered by manual "Rebuild Cache Now") skips the URL map and
    always re-downloads everything — pass startup_delay=False and call
    _clear_plugin_cache(full=True) before calling this for a true clean build.
    """
    import urllib.request

    if startup_delay:
        log('plugin cache: %ds startup delay' % STARTUP_DELAY_SECS)
        monitor = xbmc.Monitor()
        for _ in range(STARTUP_DELAY_SECS):
            if monitor.abortRequested():
                return 0
            xbmc.sleep(1000)

    log('building plugin cache from: %s  (max %d pairs)' % (plugin_path, cache_size))
    _ensure_plugin_imgfolder()

    # Load existing URL map: {url: local_path}
    url_map = {}
    try:
        if xbmcvfs.exists(PLUGIN_URLMAP):
            f = xbmcvfs.File(PLUGIN_URLMAP)
            url_map = json.load(f)
            f.close()
            log('loaded url map with %d entries' % len(url_map))
    except Exception as e:
        log('url map load failed: %s' % str(e))
        url_map = {}

    # Remove stale entries from url_map (files that no longer exist on disk)
    url_map = {u: p for u, p in url_map.items() if os.path.exists(p)}

    # Crawl the plugin and collect (fanart_url, clearlogo_url, label) per item
    pairs = []
    queue = [plugin_path]
    visited = set()

    while queue and len(pairs) < cache_size * 3:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        try:
            rpc = xbmc.executeJSONRPC(
                '{"jsonrpc":"2.0","method":"Files.GetDirectory",'
                '"params":{"directory":"%s",'
                '"properties":["art","fanart","title"],'
                '"sort":{"method":"label"}},"id":1}' % current.replace('"', '\\"').replace('\\', '\\\\')
            )
            result = json.loads(rpc)
        except Exception as e:
            log('RPC failed for %s: %s' % (current[:60], str(e)))
            continue

        if 'result' not in result or 'files' not in result.get('result', {}):
            log('no files for %s' % current[:60])
            continue

        items = result['result']['files']
        log('got %d items from %s' % (len(items), current[:60]))

        if len(pairs) == 0 and items:
            art = items[0].get('art', {})
            log('sample art keys: %s' % str(list(art.keys())))
            for k in list(art.keys())[:5]:
                log('  %s = %s' % (k, str(art[k])[:80]))

        for item in items:
            if item.get('filetype') == 'directory':
                queue.append(item['file'])
                continue

            label = item.get('label', '') or item.get('title', '')
            art   = item.get('art', {})

            fanart_url = ''
            for key in ('fanart', 'landscape'):
                raw = art.get(key) or ''
                if not raw and key == 'fanart':
                    raw = item.get('fanart', '') or ''
                if raw:
                    fanart_url = _rewrite_to_hq(raw)
                    break

            logo_url = ''
            for key in ('clearlogo', 'clearart'):
                raw = art.get(key) or ''
                if raw:
                    logo_url = _rewrite_to_hq(raw)
                    break

            if fanart_url:
                pairs.append((fanart_url, logo_url, label))

    log('collected %d candidate pairs' % len(pairs))

    def download_url(url, dest):
        """Download url to dest. Returns (ok, bytes_count)."""
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Kodi/21.0 (Windows)',
                'Accept': 'image/jpeg,image/png,image/*'
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
            if not data:
                return False, 0
            with open(dest, 'wb') as fh:
                fh.write(data)
            return True, len(data)
        except Exception as e:
            log('download failed [%s]: %s' % (url[:60], str(e)))
            return False, 0

    cached_pairs = []
    new_url_map  = {}   # build fresh map from this run's results
    seen_fanart  = set()   # guard against the same fanart URL appearing twice

    for fanart_url, logo_url, label in pairs:
        if len(cached_pairs) >= cache_size:
            break

        # Skip duplicate source URLs within a single crawl so we never cache
        # the same image twice under two different names.
        if fanart_url in seen_fanart:
            continue
        seen_fanart.add(fanart_url)

        # --- Fanart: reuse if already cached, else download ---
        # Filename is derived from a hash of the source URL so the same image
        # always maps to the same file on disk across runs. This keeps identity
        # stable, makes reuse reliable, and lets the prune step below actually
        # find and remove files that are no longer needed.
        ext = os.path.splitext(fanart_url.split('?')[0])[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp'):
            ext = '.jpg'
        fanart_file = 'fanart_%s%s' % (_url_hash(fanart_url), ext)
        fanart_dest = os.path.join(PLUGIN_FANARTDIR, fanart_file)

        reused_fanart = False
        if fanart_url in url_map and os.path.exists(url_map[fanart_url]):
            # Already have this image — reuse it
            fanart_dest = url_map[fanart_url]
            reused_fanart = True
            log('reusing fanart: %s' % os.path.basename(fanart_dest))
        elif os.path.exists(fanart_dest):
            # File already on disk from a previous run (stable name) — reuse it
            reused_fanart = True
            log('reusing fanart (on disk): %s' % os.path.basename(fanart_dest))
        else:
            ok, nbytes = download_url(fanart_url, fanart_dest)
            if not ok:
                continue
            log('downloaded %d bytes -> %s' % (nbytes, fanart_file))

        w, h = _get_image_dimensions(fanart_dest)
        log('fanart %s: %dx%d %s' % (os.path.basename(fanart_dest), w, h, label[:30]))

        if not _is_hq_16x9(w, h):
            log('discarding %s - %dx%d not HQ 16:9' % (os.path.basename(fanart_dest), w, h))
            # Only delete if we just downloaded it (not a reused file)
            if not reused_fanart:
                try:
                    os.remove(fanart_dest)
                except Exception:
                    pass
            continue

        new_url_map[fanart_url] = fanart_dest

        # --- Clearlogo: reuse if already cached, else download ---
        logo_dest = ''
        if logo_url:
            ext2 = os.path.splitext(logo_url.split('?')[0])[1].lower()
            if ext2 not in ('.jpg', '.jpeg', '.png', '.webp'):
                ext2 = '.png'
            logo_file = 'logo_%s%s' % (_url_hash(logo_url), ext2)
            logo_dest_new = os.path.join(PLUGIN_LOGODIR, logo_file)

            if logo_url in url_map and os.path.exists(url_map[logo_url]):
                logo_dest = url_map[logo_url]
                log('reusing logo: %s' % os.path.basename(logo_dest))
            elif os.path.exists(logo_dest_new):
                logo_dest = logo_dest_new
                log('reusing logo (on disk): %s' % os.path.basename(logo_dest))
            else:
                ok2, nbytes2 = download_url(logo_url, logo_dest_new)
                if ok2:
                    logo_dest = logo_dest_new
                    log('downloaded logo %d bytes -> %s' % (nbytes2, logo_file))

            if logo_dest:
                new_url_map[logo_url] = logo_dest

        cached_pairs.append([fanart_dest, logo_dest, label])
        log('PAIR CACHED %d: %s' % (len(cached_pairs), label[:30]))

    # Delete files from old url_map that are no longer needed
    needed = set(new_url_map.values())
    for old_path in url_map.values():
        if old_path not in needed and os.path.exists(old_path):
            try:
                os.remove(old_path)
                log('removed stale: %s' % os.path.basename(old_path))
            except Exception:
                pass

    # Sweep the image folders for any orphaned files that aren't part of the
    # new cache. This catches leftovers from older builds (e.g. when shrinking
    # the cache size from 100 to 10) that the URL map no longer references.
    for folder in (PLUGIN_FANARTDIR, PLUGIN_LOGODIR):
        if os.path.isdir(folder):
            try:
                for fname in os.listdir(folder):
                    fpath = os.path.join(folder, fname)
                    if fpath not in needed and os.path.isfile(fpath):
                        try:
                            os.remove(fpath)
                            log('removed orphan: %s' % fname)
                        except Exception:
                            pass
            except Exception as e:
                log('orphan sweep failed for %s: %s' % (folder, str(e)))

    # Save updated URL map
    try:
        f = xbmcvfs.File(PLUGIN_URLMAP, 'w')
        json.dump(new_url_map, f)
        f.close()
        log('url map saved: %d entries' % len(new_url_map))
    except Exception as e:
        log('failed to save url map: %s' % str(e))

    # Save index
    try:
        cf = xbmcvfs.File(PLUGIN_CACHEFILE, 'w')
        json.dump(cached_pairs, cf)
        cf.close()
    except Exception as e:
        log('failed to write cache index: %s' % str(e))

    log('plugin cache complete: %d pairs (%d new downloads)' % (
        len(cached_pairs),
        sum(1 for u in new_url_map if u not in url_map)
    ))
    return len(cached_pairs)

def read_plugin_cache():
    """Return [[fanart_path, label], ...] for the screensaver display loop.
    Index stores [fanart_path, logo_path, label] triples - we return fanart + label.
    Only includes entries where the fanart file still exists.
    """
    try:
        f = xbmcvfs.File(PLUGIN_CACHEFILE)
        entries = json.load(f)
        f.close()
        result = []
        for entry in entries:
            if len(entry) >= 3:
                fanart_path, logo_path, label = entry[0], entry[1], entry[2]
            else:
                fanart_path, label = entry[0], entry[1]
            if fanart_path and os.path.exists(fanart_path):
                result.append([fanart_path, label])
        return result
    except Exception:
        return []


def read_plugin_cache_pairs():
    """Return [[fanart_path, logo_path, label], ...] — full pairs for display with overlay."""
    try:
        f = xbmcvfs.File(PLUGIN_CACHEFILE)
        entries = json.load(f)
        f.close()
        result = []
        for entry in entries:
            if len(entry) >= 3:
                fanart_path, logo_path, label = entry[0], entry[1], entry[2]
                if fanart_path and os.path.exists(fanart_path):
                    result.append([fanart_path, logo_path, label])
        return result
    except Exception:
        return []


def plugin_cache_exists():
    return xbmcvfs.exists(PLUGIN_CACHEFILE) and bool(read_plugin_cache())

# Plugin path stored as a plain text file - avoids Kodi settings XML type issues entirely
PLUGIN_PATHFILE = os.path.join(CACHEFOLDER, 'plugin_path.txt')


def save_plugin_path(path):
    """Write the chosen plugin:// path to a plain text file."""
    if not xbmcvfs.exists(CACHEFOLDER):
        xbmcvfs.mkdir(CACHEFOLDER)
    try:
        f = xbmcvfs.File(PLUGIN_PATHFILE, 'w')
        f.write(path)
        f.close()
        log('plugin path saved: %s' % path)
    except Exception as e:
        log('failed to save plugin path: %s' % str(e))


def load_plugin_path():
    """Read the plugin:// path from the plain text file. Returns '' if not set."""
    try:
        if not xbmcvfs.exists(PLUGIN_PATHFILE):
            return ''
        f = xbmcvfs.File(PLUGIN_PATHFILE)
        path = f.read()
        f.close()
        return path.strip()
    except Exception as e:
        log('failed to load plugin path: %s' % str(e))
        return ''
