# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Download boot resource descriptions from Simplestreams repo.

This module is responsible only for syncing the repo's metadata, not the boot
resources themselves.  The two are handled in separate Simplestreams
synchronisation stages.
"""

__all__ = [
    'download_all_image_descriptions',
    ]


from provisioningserver.import_images.boot_image_mapping import (
    BootImageMapping,
)
from provisioningserver.import_images.helpers import (
    get_os_from_product,
    get_signing_policy,
    ImageSpec,
    maaslog,
)
from simplestreams.mirrors import (
    BasicMirrorWriter,
    UrlMirrorReader,
)
from simplestreams.util import (
    path_from_mirror_url,
    products_exdata,
)


def clean_up_repo_item(item):
    """Return a subset of dict `item` for storing in a boot images dict."""
    keys_to_keep = [
        'content_id', 'product_name', 'version_name', 'path', 'subarches',
        'release_codename', 'release_title', 'support_eol', 'kflavor']
    compact_item = {
        key: item[key]
        for key in keys_to_keep
        if key in item
    }
    return compact_item


class RepoDumper(BasicMirrorWriter):
    """Gather metadata about boot images available in a Simplestreams repo.

    Used inside `download_image_descriptions`.  Stores basic metadata about
    each image it finds upstream in a given `BootImageMapping`.  Each stored
    item is a dict containing the basic metadata for retrieving a boot image.

    Simplestreams' `BasicMirrorWriter` in itself is stateless.  It relies on
    a subclass (such as this one) to store data.

    :ivar boot_images_dict: A `BootImageMapping`.  Image metadata will be
        stored here as it is discovered.  Simplestreams does not interact with
        this variable.
    """

    def __init__(self, boot_images_dict):
        super(RepoDumper, self).__init__(config={
            # Only download the latest version. Without this all versions
            # will be read, causing miss matches in versions.
            'max_items': 1,
            })
        self.boot_images_dict = boot_images_dict

    def load_products(self, path=None, content_id=None):
        """Overridable from `BasicMirrorWriter`."""
        # It looks as if this method only makes sense for MirrorReaders, not
        # for MirrorWriters.  The default MirrorWriter implementation just
        # raises NotImplementedError.  Stop it from doing that.
        return

    def insert_item(self, data, src, target, pedigree, contentsource):
        """Overridable from `BasicMirrorWriter`."""
        item = products_exdata(src, pedigree)
        os = get_os_from_product(item)
        arch, subarches = item['arch'], item['subarches']
        release = item['release']
        label = item['label']
        base_image = ImageSpec(os, arch, None, release, label)
        compact_item = clean_up_repo_item(item)
        for subarch in subarches.split(','):
            self.boot_images_dict.setdefault(
                base_image._replace(subarch=subarch), compact_item)

        # HWE resources need to map to a specfic resource, and not just to
        # any of the supported subarchitectures for that resource.
        subarch = item['subarch']
        self.boot_images_dict.set(
            base_image._replace(subarch=subarch), compact_item)

        if os == 'ubuntu' and item.get('version') is not None:
            # HWE resources with generic, should map to the HWE that ships with
            # that release. Starting with Xenial kernels changed from using the
            # naming format hwe-<letter> to hwe-<release>. Look for both.
            hwe_archs = ["hwe-%s" % item['version'], "hwe-%s" % release[0]]
            if subarch in hwe_archs and 'generic' in subarches:
                self.boot_images_dict.set(
                    base_image._replace(subarch='generic'), compact_item)

    def sync(self, reader, path):
        try:
            super(RepoDumper, self).sync(reader, path)
        except IOError:
            maaslog.warning(
                "I/O error while syncing boot images. If this problem "
                "persists, verify network connectivity and disk usage.")


def value_passes_filter_list(filter_list, property_value):
    """Does the given property of a boot image pass the given filter list?

    The value passes if either it matches one of the entries in the list of
    filter values, or one of the filter values is an asterisk (`*`).
    """
    return '*' in filter_list or property_value in filter_list


def value_passes_filter(filter_value, property_value):
    """Does the given property of a boot image pass the given filter?

    The value passes the filter if either the filter value is an asterisk
    (`*`) or the value is equal to the filter value.
    """
    return filter_value in ('*', property_value)


def image_passes_filter(filters, os, arch, subarch, release, label):
    """Filter a boot image against configured import filters.

    :param filters: A list of dicts describing the filters, as in `boot_merge`.
        If the list is empty, or `None`, any image matches.  Any entry in a
        filter may be a string containing just an asterisk (`*`) to denote that
        the entry will match any value.
    :param os: The given boot image's operating system.
    :param arch: The given boot image's architecture.
    :param subarch: The given boot image's subarchitecture.
    :param release: The given boot image's OS release.
    :param label: The given boot image's label.
    :return: Whether the image matches any of the dicts in `filters`.
    """
    if filters is None or len(filters) == 0:
        return True
    for filter_dict in filters:
        item_matches = (
            value_passes_filter(filter_dict['os'], os) and
            value_passes_filter(filter_dict['release'], release) and
            value_passes_filter_list(filter_dict['arches'], arch) and
            value_passes_filter_list(filter_dict['subarches'], subarch) and
            value_passes_filter_list(filter_dict['labels'], label)
        )
        if item_matches:
            return True
    return False


def boot_merge(destination, additions, filters=None):
    """Complement one `BootImageMapping` with entries from another.

    This adds entries from `additions` (that match `filters`, if given) to
    `destination`, but only for those image specs for which `destination` does
    not have entries yet.

    :param destination: `BootImageMapping` to be updated.  It will be extended
        in-place.
    :param additions: A second `BootImageMapping`, which will be used as a
        source of additional entries.
    :param filters: List of dicts, each of which contains 'os', arch',
        'subarch', 'release', and 'label' keys.  If given, entries are only
        considered for copying from `additions` to `destination` if they match
        at least one of the filters.  Entries in the filter may be the string
        `*` (or for entries that are lists, may contain the string `*`) to make
        them match any value.
    """
    for image, resource in additions.items():
        os, arch, subarch, release, label = image
        if image_passes_filter(
                filters, os, arch, subarch, release, label):
            # Do not override an existing entry with the same
            # os/arch/subarch/release/label: the first entry found takes
            # precedence.
            destination.setdefault(image, resource)


def download_image_descriptions(path, keyring=None, user_agent=None):
    """Download image metadata from upstream Simplestreams repo.

    :param path: The path to a Simplestreams repo.
    :param keyring: Optional keyring for verifying the repo's signatures.
    :param user_agent: Optional user agent string for downloading the image
        descriptions.
    :return: A `BootImageMapping` describing available boot resources.
    """
    maaslog.info("Downloading image descriptions from %s", path)
    mirror, rpath = path_from_mirror_url(path, None)
    policy = get_signing_policy(rpath, keyring)
    if user_agent is None:
        reader = UrlMirrorReader(mirror, policy=policy)
    else:
        try:
            reader = UrlMirrorReader(
                mirror, policy=policy, user_agent=user_agent)
        except TypeError:
            # UrlMirrorReader doesn't support the user_agent argument.
            # simplestream >=bzr429 is required for this feature.
            reader = UrlMirrorReader(mirror, policy=policy)

    boot_images_dict = BootImageMapping()
    dumper = RepoDumper(boot_images_dict)
    dumper.sync(reader, rpath)
    return boot_images_dict


def download_all_image_descriptions(sources, user_agent=None):
    """Download image metadata for all sources in `config`."""
    boot = BootImageMapping()
    for source in sources:
        repo_boot = download_image_descriptions(
            source['url'], keyring=source.get('keyring', None),
            user_agent=None)
        boot_merge(boot, repo_boot, source['selections'])
    return boot
