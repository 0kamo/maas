# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Utilities that BootMethod's can use.

XXX: This needs a LOT more documentation.

"""

__all__ = [
    'get_distro_release',
    'get_package',
    'get_updates_package',
    ]

import gzip
import hashlib
import io
import os
from platform import linux_distribution
import re
import urllib.error
import urllib.parse
import urllib.request

from provisioningserver.import_images.helpers import maaslog
from provisioningserver.utils.fs import tempdir
from provisioningserver.utils.shell import call_and_check


def urljoin(*args):
    return '/'.join(s.strip('/') for s in args)


def get_distro_release():
    """Returns the release name for the current distribution."""
    distname, version, codename = linux_distribution()
    return codename


def get_file(url):
    """Downloads the file from the URL.

    :param url: URL to download file
    :return: File data, or None
    """
    # Build a new opener so that the environment is checked for proxy
    # URLs. Using urllib2.urlopen() means that we'd only be using the
    # proxies as defined when urlopen() was called the first time.
    try:
        response = urllib.request.build_opener().open(url)
        return response.read()
    except urllib.error.URLError as e:
        maaslog.error("Unable to download %s: %s", url, str(e.reason))
        raise
    except BaseException as e:
        maaslog.error("Unable to download %s: %s", url, str(e))
        raise


def get_md5sum(data):
    """Returns the md5sum for the provided data."""
    md5 = hashlib.md5()
    md5.update(data)
    return md5.hexdigest()


def gpg_verify_data(signature, data_file):
    """Verify's data using the signature."""
    with tempdir() as tmp:
        sig_out = os.path.join(tmp, 'verify.gpg')
        with open(sig_out, 'wb') as stream:
            stream.write(signature)

        data_out = os.path.join(tmp, 'verify')
        with open(data_out, 'wb') as stream:
            stream.write(data_file)

        call_and_check([
            "gpgv",
            "--keyring",
            "/etc/apt/trusted.gpg",
            sig_out,
            data_out
            ])


def decompress_packages(packages):
    compressed = io.BytesIO(packages)
    decompressed = gzip.GzipFile(fileobj=compressed)
    return str(decompressed.read(), errors='ignore')


def get_packages(archive, component, architecture, release=None):
    """Gets the packages list from the archive."""
    release = get_distro_release() if release is None else release
    url = urljoin(archive, 'dists', release)
    release_url = urljoin(url, 'Release')
    release_file = get_file(release_url)
    release_file_gpg = get_file('%s.gpg' % release_url)
    gpg_verify_data(release_file_gpg, release_file)

    # Download the packages and verify that md5sum matches
    path = '%s/binary-%s/Packages.gz' % (component, architecture)
    packages_url = urljoin(url, path)
    packages = get_file(packages_url)
    md5sum = re.search(
        rb"^\s*?([a-zA-Z0-9]{32})\s+?[0-9]+\s+%s$" % path.encode("utf-8"),
        release_file,
        re.MULTILINE).group(1)
    if get_md5sum(packages).encode("utf-8") != md5sum:
        raise ValueError("%s failed checksum." % packages_url)

    return decompress_packages(packages)


def get_package_info(package, archive, component, architecture, release=None):
    """Gets the package information."""
    release = get_distro_release() if release is None else release
    packages = get_packages(archive, component, architecture, release=release)

    info = re.search(
        r"^(Package: %s.*?)\n\n" % package,  # XXX: Escape `package`?
        packages,
        re.MULTILINE | re.DOTALL)
    if info is None:
        return None
    info = info.group(1)

    data = {}
    for line in info.splitlines():
        key, value = line.split(':', 1)
        data[key] = value.strip()
    return data


def get_package(package, archive, component, architecture, release=None):
    """Downloads the package from the archive.

    :return: A ``(bytes, str)`` tuple where ``bytes`` is the raw Debian
        package data, and ``str`` is the package file name.
    """
    release = get_distro_release() if release is None else release
    package = get_package_info(
        package, archive, component, architecture, release=release)
    if package is None:
        return None, None

    # Download the package and check checksum
    path = package['Filename']
    filename = os.path.basename(path)
    url = urljoin(archive, path)
    deb = get_file(url)
    md5 = get_md5sum(deb)
    if md5 != package['MD5sum']:
        raise ValueError("%s failed checksum." % filename)
    return deb, filename


def get_updates_package(
        package, archive, component, architecture, release=None):
    """Downloads the package from the {release}-updates if it exists, if not
    fails back to {release} archive.

    :return: A ``(bytes, str)`` tuple where ``bytes`` is the raw Debian
        package data, and ``str`` is the package file name.
    """
    release = get_distro_release() if release is None else release
    releases = ['%s-updates' % release, release]
    for release in releases:
        deb, filename = get_package(
            package, archive, component, architecture, release=release)
        if deb is not None:
            return deb, filename
    return None, None
