"""
    SEA6ULL Repository Generator
    
    Place this script in the root of your repository.sea6ull folder.
    It will zip up all addon folders for each supported Kodi version,
    place the zips in the zips/<version>/ folder, and regenerate the
    addons.xml and addons.xml.md5 files for each version.

    Supported versions:
        omega  - Kodi 21
        piers  - Kodi 22

    To add a new Kodi version in the future:
        1. Add the version name to KODI_VERSIONS below
        2. Create a folder with that name at the repo root
        3. Add your addon folders inside it
        4. Run this script
"""

import re
import os
import shutil
import hashlib
import zipfile
from xml.etree import ElementTree

KODI_VERSIONS = ["omega", "piers"]

IGNORE = [
    ".git",
    ".github",
    ".gitignore",
    ".DS_Store",
    "thumbs.db",
    ".idea",
    "venv",
    "__pycache__",
    "*.pyc",
    "*.pyo",
]


class Generator:
    """
    Generates a new addons.xml file from each addon's addon.xml file
    and a new addons.xml.md5 hash file. Must be run from the root of
    the repository.
    """

    def __init__(self, release):
        self.release_path = release
        self.zips_path = os.path.join("zips", release)

        if not os.path.exists(self.zips_path):
            os.makedirs(self.zips_path)

        self._remove_binaries()
        self._generate_addons_file()
        self._generate_md5_file()

    def _remove_binaries(self):
        """
        Removes any and all compiled Python files before operations.
        """
        for parent, dirnames, filenames in os.walk(self.release_path):
            for fn in filenames:
                if fn.lower().endswith("pyo") or fn.lower().endswith("pyc"):
                    compiled = os.path.join(parent, fn)
                    try:
                        os.remove(compiled)
                        print("Removed compiled python file: {}".format(compiled))
                    except:
                        print("Failed to remove compiled python file: {}".format(compiled))
            for dir in dirnames:
                if "pycache" in dir.lower():
                    compiled = os.path.join(parent, dir)
                    try:
                        shutil.rmtree(compiled)
                        print("Removed __pycache__ folder: {}".format(compiled))
                    except:
                        print("Failed to remove __pycache__ folder: {}".format(compiled))

    def _create_zip(self, addon_id, version):
        """
        Creates a zip file in zips/<kodi_version>/<addon_id>/ for the given addon.
        """
        addon_folder = os.path.join(self.release_path, addon_id)
        zip_folder = os.path.join(self.zips_path, addon_id)

        if not os.path.exists(zip_folder):
            os.makedirs(zip_folder)

        final_zip = os.path.join(zip_folder, "{0}-{1}.zip".format(addon_id, version))
        if not os.path.exists(final_zip):
            print("Creating zip: {0} v{1}".format(addon_id, version))
            zf = zipfile.ZipFile(final_zip, "w", compression=zipfile.ZIP_DEFLATED)
            root_len = len(os.path.dirname(os.path.abspath(addon_folder)))

            for root, dirs, files in os.walk(addon_folder):
                # Remove ignored folders in-place so os.walk skips them
                dirs[:] = [d for d in dirs if d not in IGNORE and not d.startswith(".")]
                files = [f for f in files if not any(f.startswith(i) or f == i for i in IGNORE)]

                archive_root = os.path.abspath(root)[root_len:]
                for f in files:
                    fullpath = os.path.join(root, f)
                    archive_name = os.path.join(archive_root, f)
                    zf.write(fullpath, archive_name, zipfile.ZIP_DEFLATED)

            zf.close()
            print("  -> {}".format(final_zip))
        else:
            print("Zip already exists, skipping: {0} v{1}".format(addon_id, version))

    def _copy_meta_files(self, addon_id, zip_addon_folder):
        """
        Copies addon.xml and art assets (icon, fanart) into the zips folder.
        """
        tree = ElementTree.parse(os.path.join(self.release_path, addon_id, "addon.xml"))
        root = tree.getroot()

        copyfiles = ["addon.xml"]
        for ext in root.findall("extension"):
            if ext.get("point") == "xbmc.addon.metadata":
                assets = ext.find("assets")
                if not assets:
                    continue
                for art in assets:
                    copyfiles.append(os.path.normpath(art.text))

        src_folder = os.path.join(self.release_path, addon_id)
        for file in copyfiles:
            src = os.path.join(src_folder, file)
            dst = os.path.join(zip_addon_folder, file)
            dst_dir = os.path.dirname(dst)
            if not os.path.exists(dst_dir):
                os.makedirs(dst_dir)
            if os.path.exists(src):
                shutil.copy(src, dst)

    def _generate_addons_file(self):
        """
        Generates a zip for each addon found and updates addons.xml.
        """
        addons_xml = '<?xml version="1.0" encoding="UTF-8"?>\n<addons>\n'

        folders = [
            i for i in os.listdir(self.release_path)
            if os.path.isdir(os.path.join(self.release_path, i))
            and not i.startswith(".")
            and os.path.exists(os.path.join(self.release_path, i, "addon.xml"))
        ]

        if not folders:
            print("WARNING: No addon folders found in '{}'".format(self.release_path))

        for addon in sorted(folders):
            try:
                _path = os.path.join(self.release_path, addon, "addon.xml")
                xml_lines = open(_path, "r", encoding="utf-8").read().splitlines()
                addon_xml = ""
                ver_found = False

                for line in xml_lines:
                    if line.find("<?xml") >= 0:
                        continue
                    if 'version="' in line and not ver_found:
                        version = re.compile('version="(.+?)"').findall(line)[0]
                        ver_found = True
                    addon_xml += line.rstrip() + "\n"

                addons_xml += addon_xml.rstrip() + "\n\n"
                self._create_zip(addon, version)
                self._copy_meta_files(addon, os.path.join(self.zips_path, addon))

            except Exception as e:
                print("ERROR - Excluding {0}: {1}".format(addon, e))

        addons_xml = addons_xml.strip() + "\n</addons>\n"
        out_path = os.path.join(self.zips_path, "addons.xml")
        self._save_file(addons_xml.encode("utf-8"), file=out_path, decode=True)
        print("Successfully updated {}".format(out_path))

    def _generate_md5_file(self):
        """
        Generates addons.xml.md5.
        """
        try:
            addons_xml_path = os.path.join(self.zips_path, "addons.xml")
            m = hashlib.md5(
                open(addons_xml_path, "r", encoding="utf-8").read().encode("utf-8")
            ).hexdigest()
            self._save_file(m, file=os.path.join(self.zips_path, "addons.xml.md5"))
            print("Successfully updated {}/addons.xml.md5".format(self.zips_path))
        except Exception as e:
            print("ERROR creating addons.xml.md5: {}".format(e))

    def _save_file(self, data, file, decode=False):
        """
        Saves a file.
        """
        try:
            if decode:
                open(file, "w", encoding="utf-8").write(data.decode("utf-8"))
            else:
                open(file, "w").write(data)
        except Exception as e:
            print("ERROR saving {0}: {1}".format(file, e))


if __name__ == "__main__":
    print("=" * 50)
    print("SEA6ULL Repository Generator")
    print("=" * 50)

    found = [v for v in KODI_VERSIONS if os.path.exists(v)]
    if not found:
        print("ERROR: No version folders found. Expected one or more of: {}".format(KODI_VERSIONS))
    else:
        for version in found:
            print("\nProcessing Kodi version: {}".format(version.upper()))
            print("-" * 50)
            Generator(version)

    print("\nDone.")
