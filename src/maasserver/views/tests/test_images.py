# Copyright 2014-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test maasserver images views."""

__all__ = []

import datetime
import http.client
import json
import random

from django.conf import settings
from django.core.urlresolvers import reverse
from lxml.html import fromstring
from maasserver.enum import (
    BOOT_RESOURCE_TYPE,
    NODE_STATUS,
)
from maasserver.models import (
    BootResource,
    BootSourceCache,
    BootSourceSelection,
    Config,
)
from maasserver.models.signals import bootsources
from maasserver.testing import extract_redirect
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.utils.converters import human_readable_bytes
from maasserver.utils.orm import (
    get_one,
    reload_object,
)
from maasserver.views import images as images_view
from maastesting.matchers import (
    MockCalledOnceWith,
    MockCalledWith,
)
from requests import ConnectionError
from testtools.matchers import (
    ContainsAll,
    HasLength,
)


class UbuntuImagesTest(MAASServerTestCase):

    def setUp(self):
        super(UbuntuImagesTest, self).setUp()
        # Disable boot source cache signals.
        self.addCleanup(bootsources.signals.enable)
        bootsources.signals.disable()

    def patch_get_os_info_from_boot_sources(
            self, sources, releases=None, arches=None):
        if releases is None:
            releases = [factory.make_name('release') for _ in range(3)]
        if arches is None:
            arches = [factory.make_name('arch') for _ in range(3)]
        mock_get_os_info = self.patch(
            images_view, 'get_os_info_from_boot_sources')
        mock_get_os_info.return_value = (sources, releases, arches)
        return mock_get_os_info

    def test_shows_connection_error(self):
        self.client_log_in(as_admin=True)
        mock_get_os_info = self.patch(
            images_view, 'get_os_info_from_boot_sources')
        mock_get_os_info.side_effect = ConnectionError()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        warnings = doc.cssselect('div#connection-error')
        self.assertEqual(1, len(warnings))

    def test_shows_no_ubuntu_sources(self):
        self.client_log_in(as_admin=True)
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        warnings = doc.cssselect('div#no-ubuntu-sources')
        self.assertEqual(1, len(warnings))

    def test_shows_too_many_ubuntu_sources(self):
        self.client_log_in(as_admin=True)
        sources = [factory.make_BootSource() for _ in range(2)]
        self.patch_get_os_info_from_boot_sources(sources)
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        warnings = doc.cssselect('div#too-many-ubuntu-sources')
        self.assertEqual(1, len(warnings))

    def test_shows_release_options(self):
        self.client_log_in(as_admin=True)
        sources = [factory.make_BootSource()]
        releases = [factory.make_name('release') for _ in range(3)]
        self.patch_get_os_info_from_boot_sources(sources, releases=releases)
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        releases_content = doc.cssselect(
            'ul#ubuntu-releases')[0].text_content()
        self.assertThat(releases_content, ContainsAll(releases))

    def test_shows_architecture_options(self):
        self.client_log_in(as_admin=True)
        sources = [factory.make_BootSource()]
        arches = [factory.make_name('arch') for _ in range(3)]
        self.patch_get_os_info_from_boot_sources(sources, arches=arches)
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        arches_content = doc.cssselect(
            'ul#ubuntu-arches')[0].text_content()
        self.assertThat(arches_content, ContainsAll(arches))

    def test_shows_missing_images_warning_if_not_ubuntu_boot_resources(self):
        self.client_log_in()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        warnings = doc.cssselect('div#missing-ubuntu-images')
        self.assertEqual(1, len(warnings))

    def test_hides_import_button_if_not_admin(self):
        self.client_log_in()
        sources = [factory.make_BootSource()]
        self.patch_get_os_info_from_boot_sources(sources)
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        import_button = doc.cssselect(
            '#ubuntu-images')[0].cssselect('input[type="submit"]')
        self.assertEqual(0, len(import_button))

    def test_shows_import_button_if_admin(self):
        self.client_log_in(as_admin=True)
        sources = [factory.make_BootSource()]
        self.patch_get_os_info_from_boot_sources(sources)
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        import_button = doc.cssselect(
            '#ubuntu-images')[0].cssselect('input[type="submit"]')
        self.assertEqual(1, len(import_button))

    def test_post_returns_forbidden_if_not_admin(self):
        self.client_log_in()
        response = self.client.post(
            reverse('images'), {'ubuntu_images': 1})
        self.assertEqual(http.client.FORBIDDEN, response.status_code)

    def test_import_calls_import_resources(self):
        self.client_log_in(as_admin=True)
        sources = [factory.make_BootSource()]
        self.patch_get_os_info_from_boot_sources(sources)
        mock_import = self.patch(images_view, 'import_resources')
        response = self.client.post(
            reverse('images'), {'ubuntu_images': 1})
        self.assertEqual(http.client.FOUND, response.status_code)
        self.assertThat(mock_import, MockCalledOnceWith())

    def test_import_sets_empty_selections(self):
        self.client_log_in(as_admin=True)
        source = factory.make_BootSource()
        self.patch_get_os_info_from_boot_sources([source])
        self.patch(images_view, 'import_resources')
        response = self.client.post(
            reverse('images'), {'ubuntu_images': 1})
        self.assertEqual(http.client.FOUND, response.status_code)

        selections = BootSourceSelection.objects.filter(boot_source=source)
        self.assertThat(selections, HasLength(1))
        self.assertEqual(
            (selections[0].os, selections[0].release,
                selections[0].arches, selections[0].subarches,
                selections[0].labels),
            ("ubuntu", "", [], ["*"], ["*"]))

    def test_import_sets_release_selections(self):
        self.client_log_in(as_admin=True)
        source = factory.make_BootSource()
        releases = [factory.make_name('release') for _ in range(3)]
        self.patch_get_os_info_from_boot_sources([source])
        self.patch(images_view, 'import_resources')
        response = self.client.post(
            reverse('images'), {'ubuntu_images': 1, 'release': releases})
        self.assertEqual(http.client.FOUND, response.status_code)

        selections = BootSourceSelection.objects.filter(boot_source=source)
        self.assertThat(selections, HasLength(len(releases)))
        self.assertItemsEqual(
            releases,
            [selection.release for selection in selections])

    def test_import_sets_arches_on_selections(self):
        self.client_log_in(as_admin=True)
        source = factory.make_BootSource()
        releases = [factory.make_name('release') for _ in range(3)]
        arches = [factory.make_name('arches') for _ in range(3)]
        self.patch_get_os_info_from_boot_sources([source])
        self.patch(images_view, 'import_resources')
        response = self.client.post(
            reverse('images'),
            {'ubuntu_images': 1, 'release': releases, 'arch': arches})
        self.assertEqual(http.client.FOUND, response.status_code)

        selections = BootSourceSelection.objects.filter(boot_source=source)
        self.assertThat(selections, HasLength(len(releases)))
        self.assertItemsEqual(
            [arches, arches, arches],
            [selection.arches for selection in selections])

    def test_import_removes_old_selections(self):
        self.client_log_in(as_admin=True)
        source = factory.make_BootSource()
        release = factory.make_name('release')
        delete_selection = BootSourceSelection.objects.create(
            boot_source=source, os='ubuntu',
            release=factory.make_name('release'))
        keep_selection = BootSourceSelection.objects.create(
            boot_source=source, os='ubuntu', release=release)
        self.patch_get_os_info_from_boot_sources([source])
        self.patch(images_view, 'import_resources')
        response = self.client.post(
            reverse('images'), {'ubuntu_images': 1, 'release': [release]})
        self.assertEqual(http.client.FOUND, response.status_code)
        self.assertIsNone(reload_object(delete_selection))
        self.assertIsNotNone(reload_object(keep_selection))


class OtherImagesTest(MAASServerTestCase):

    def setUp(self):
        super(OtherImagesTest, self).setUp()
        # Disable boot source cache signals.
        self.addCleanup(bootsources.signals.enable)
        bootsources.signals.disable()

    def make_other_resource(self, os=None, arch=None, subarch=None,
                            release=None):
        if os is None:
            os = factory.make_name('os')
        if arch is None:
            arch = factory.make_name('arch')
        if subarch is None:
            subarch = factory.make_name('subarch')
        if release is None:
            release = factory.make_name('release')
        name = '%s/%s' % (os, release)
        architecture = '%s/%s' % (arch, subarch)
        resource = factory.make_BootResource(
            rtype=BOOT_RESOURCE_TYPE.SYNCED,
            name=name, architecture=architecture)
        resource_set = factory.make_BootResourceSet(resource)
        factory.make_boot_resource_file_with_content(resource_set)
        return resource

    def test_hides_other_synced_images_section(self):
        self.client_log_in()
        BootSourceCache.objects.all().delete()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        section = doc.cssselect('div#other-sync-images')
        self.assertEqual(
            0, len(section), "Didn't hide the other images section.")

    def test_shows_other_synced_images_section(self):
        self.client_log_in(as_admin=True)
        factory.make_BootSourceCache()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        section = doc.cssselect('div#other-sync-images')
        self.assertEqual(
            1, len(section), "Didn't show the other images section.")

    def test_hides_image_from_boot_source_cache_without_admin(self):
        self.client_log_in()
        factory.make_BootSourceCache()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        rows = doc.cssselect('table#other-resources > tbody > tr')
        self.assertEqual(
            0, len(rows), "Didn't hide unselected boot image from non-admin.")

    def test_shows_image_from_boot_source_cache_with_admin(self):
        self.client_log_in(as_admin=True)
        cache = factory.make_BootSourceCache()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        title = doc.cssselect(
            'table#other-resources > tbody > '
            'tr > td')[1].text_content().strip()
        self.assertEqual('%s/%s' % (cache.os, cache.release), title)

    def test_shows_checkbox_for_boot_source_cache(self):
        self.client_log_in(as_admin=True)
        factory.make_BootSourceCache()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        checkbox = doc.cssselect(
            'table#other-resources > tbody > tr > td > input')
        self.assertEqual(
            1, len(checkbox), "Didn't show checkbox for boot image.")

    def test_shows_last_update_time_for_synced_resource(self):
        self.client_log_in(as_admin=True)
        cache = factory.make_BootSourceCache()
        self.make_other_resource(
            os=cache.os, arch=cache.arch,
            subarch=cache.subarch, release=cache.release)
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        last_update = doc.cssselect(
            'table#other-resources > tbody > '
            'tr > td')[5].text_content().strip()
        dt = datetime.datetime.strptime(last_update, '%a, %d %b. %Y %H:%M:%S')
        # TimestampedModel includes microseconds which we don't print. To
        # confirm the right time is returned its easiest to just compare the
        # ctimes
        self.assertEquals(cache.updated.ctime(), dt.ctime())

    def test_shows_number_of_nodes_for_synced_resource(self):
        self.client_log_in(as_admin=True)
        cache = factory.make_BootSourceCache()
        resource = self.make_other_resource(
            os=cache.os, arch=cache.arch,
            subarch=cache.subarch, release=cache.release)
        factory.make_Node(
            status=NODE_STATUS.DEPLOYED,
            osystem=cache.os, distro_series=cache.release,
            architecture=resource.architecture)
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        number_of_nodes = doc.cssselect(
            'table#other-resources > tbody > '
            'tr > td')[4].text_content().strip()
        self.assertEqual(
            1, int(number_of_nodes),
            "Incorrect number of deployed nodes for resource.")

    def test_shows_apply_button_if_admin(self):
        self.client_log_in(as_admin=True)
        factory.make_BootSourceCache()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        apply_button = doc.cssselect(
            '#other-sync-images')[0].cssselect('input[type="submit"]')
        self.assertEqual(
            1, len(apply_button), "Didn't show apply button for admin.")

    def test_hides_apply_button_if_import_running(self):
        self.client_log_in(as_admin=True)
        factory.make_BootSourceCache()
        self.patch(
            images_view, 'is_import_resources_running').return_value = True
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        apply_button = doc.cssselect(
            '#other-sync-images')[0].cssselect('input[type="submit"]')
        self.assertEqual(
            0, len(apply_button),
            "Didn't hide apply button when import running.")

    def test_calls_get_os_release_title_for_other_resource(self):
        self.client_log_in()
        title = factory.make_name('title')
        cache = factory.make_BootSourceCache()
        resource = self.make_other_resource(
            os=cache.os, arch=cache.arch,
            subarch=cache.subarch, release=cache.release)
        mock_get_title = self.patch(images_view, 'get_os_release_title')
        mock_get_title.return_value = title
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        row_title = doc.cssselect(
            'table#other-resources > tbody > '
            'tr > td')[1].text_content().strip()
        self.assertEqual(title, row_title)
        os, release = resource.name.split('/')
        self.assertThat(mock_get_title, MockCalledWith(os, release))

    def test_post_returns_forbidden_if_not_admin(self):
        self.client_log_in()
        response = self.client.post(
            reverse('images'), {'other_images': 1})
        self.assertEqual(http.client.FORBIDDEN, response.status_code)

    def test_post_clears_all_other_os_selections(self):
        self.client_log_in(as_admin=True)
        source = factory.make_BootSource()
        ubuntu_selection = BootSourceSelection.objects.create(
            boot_source=source, os='ubuntu')
        other_selection = BootSourceSelection.objects.create(
            boot_source=source, os=factory.make_name('os'))
        self.patch(images_view, 'import_resources')
        response = self.client.post(
            reverse('images'), {'other_images': 1, 'image': []})
        self.assertEqual(http.client.FOUND, response.status_code)
        self.assertIsNotNone(reload_object(ubuntu_selection))
        self.assertIsNone(reload_object(other_selection))

    def test_post_creates_selection_with_multiple_arches(self):
        self.client_log_in(as_admin=True)
        source = factory.make_BootSource()
        os = factory.make_name('os')
        release = factory.make_name('release')
        arches = [factory.make_name('arch') for _ in range(3)]
        images = []
        for arch in arches:
            factory.make_BootSourceCache(
                boot_source=source, os=os, release=release, arch=arch)
            images.append('%s/%s/subarch/%s' % (os, arch, release))
        self.patch(images_view, 'import_resources')
        response = self.client.post(
            reverse('images'), {'other_images': 1, 'image': images})
        self.assertEqual(http.client.FOUND, response.status_code)

        selection = get_one(BootSourceSelection.objects.filter(
            boot_source=source, os=os, release=release))
        self.assertIsNotNone(selection)
        self.assertItemsEqual(arches, selection.arches)

    def test_post_calls_import_resources(self):
        self.client_log_in(as_admin=True)
        mock_import = self.patch(images_view, 'import_resources')
        response = self.client.post(
            reverse('images'), {'other_images': 1, 'image': []})
        self.assertEqual(http.client.FOUND, response.status_code)
        self.assertThat(mock_import, MockCalledOnceWith())


class GeneratedImagesTest(MAASServerTestCase):

    def make_generated_resource(self, os=None, arch=None, subarch=None,
                                release=None):
        if os is None:
            os = factory.make_name('os')
        if arch is None:
            arch = factory.make_name('arch')
        if subarch is None:
            subarch = factory.make_name('subarch')
        if release is None:
            release = factory.make_name('release')
        name = '%s/%s' % (os, release)
        architecture = '%s/%s' % (arch, subarch)
        resource = factory.make_BootResource(
            rtype=BOOT_RESOURCE_TYPE.GENERATED,
            name=name, architecture=architecture)
        resource_set = factory.make_BootResourceSet(resource)
        factory.make_boot_resource_file_with_content(resource_set)
        return resource

    def test_hides_generated_images_section(self):
        self.client_log_in()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        section = doc.cssselect('div#generated-images')
        self.assertEqual(
            0, len(section), "Didn't hide the generated images section.")

    def test_shows_generated_images_section(self):
        self.client_log_in()
        self.make_generated_resource()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        section = doc.cssselect('div#generated-images')
        self.assertEqual(
            1, len(section), "Didn't show the generated images section.")

    def test_shows_generated_resources(self):
        self.client_log_in()
        resources = [self.make_generated_resource() for _ in range(3)]
        names = [resource.name for resource in resources]
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        table_content = doc.cssselect(
            'table#generated-resources')[0].text_content()
        self.assertThat(table_content, ContainsAll(names))

    def test_shows_delete_button_for_generated_resource(self):
        self.client_log_in(as_admin=True)
        self.make_generated_resource()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        delete_btn = doc.cssselect(
            'table#generated-resources > tbody > tr > td > '
            'a[title="Delete image"]')
        self.assertEqual(
            1, len(delete_btn),
            "Didn't show delete button for generated image.")

    def test_shows_last_update_time_for_synced_resource(self):
        self.client_log_in(as_admin=True)
        resource = self.make_generated_resource()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        last_update = doc.cssselect(
            'table#generated-resources > tbody > '
            'tr > td')[5].text_content().strip()
        dt = datetime.datetime.strptime(last_update, '%a, %d %b. %Y %H:%M:%S')
        # TimestampedModel includes microseconds which we don't print. To
        # confirm the right time is returned its easiest to just compare the
        # ctimes
        self.assertEquals(resource.updated.ctime(), dt.ctime())

    def test_hides_delete_button_for_generated_resource_when_not_admin(self):
        self.client_log_in()
        self.make_generated_resource()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        delete_btn = doc.cssselect(
            'table#generated-resources > tbody > tr > td > '
            'a[title="Delete image"]')
        self.assertEqual(
            0, len(delete_btn),
            "Didn't hide delete button for generated image when not admin.")

    def test_calls_get_os_release_title_for_generated_resource(self):
        self.client_log_in()
        title = factory.make_name('title')
        resource = self.make_generated_resource()
        mock_get_title = self.patch(images_view, 'get_os_release_title')
        mock_get_title.return_value = title
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        row_title = doc.cssselect(
            'table#generated-resources > tbody > '
            'tr > td')[1].text_content().strip()
        self.assertEqual(title, row_title)
        os, release = resource.name.split('/')
        self.assertThat(mock_get_title, MockCalledOnceWith(os, release))


class UploadedImagesTest(MAASServerTestCase):

    def make_uploaded_resource(self, name=None):
        if name is None:
            name = factory.make_name('name')
        arch = factory.make_name('arch')
        subarch = factory.make_name('subarch')
        architecture = '%s/%s' % (arch, subarch)
        resource = factory.make_BootResource(
            rtype=BOOT_RESOURCE_TYPE.UPLOADED,
            name=name, architecture=architecture)
        resource_set = factory.make_BootResourceSet(resource)
        factory.make_boot_resource_file_with_content(resource_set)
        return resource

    def test_shows_no_custom_images_message(self):
        self.client_log_in()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        warnings = doc.cssselect('div#no-custom-images')
        self.assertEqual(1, len(warnings))

    def test_shows_uploaded_resources(self):
        self.client_log_in()
        names = [factory.make_name('name') for _ in range(3)]
        [self.make_uploaded_resource(name) for name in names]
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        table_content = doc.cssselect(
            'table#uploaded-resources')[0].text_content()
        self.assertThat(table_content, ContainsAll(names))

    def test_shows_uploaded_resources_name_if_title_blank(self):
        self.client_log_in()
        name = factory.make_name('name')
        resource = self.make_uploaded_resource(name)
        resource.extra['title'] = ''
        resource.save()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        name_col = doc.cssselect(
            'table#uploaded-resources > tbody > tr > td')[1].text_content()
        self.assertEqual(name, name_col.strip())

    def test_shows_last_update_time_for_synced_resource(self):
        self.client_log_in(as_admin=True)
        resource = self.make_uploaded_resource()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        last_update = doc.cssselect(
            'table#uploaded-resources > tbody > '
            'tr > td')[5].text_content().strip()
        dt = datetime.datetime.strptime(last_update, '%a, %d %b. %Y %H:%M:%S')
        # TimestampedModel includes microseconds which we don't print. To
        # confirm the right time is returned its easiest to just compare the
        # ctimes
        self.assertEquals(resource.updated.ctime(), dt.ctime())

    def test_shows_delete_button_for_uploaded_resource(self):
        self.client_log_in(as_admin=True)
        self.make_uploaded_resource()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        delete_btn = doc.cssselect(
            'table#uploaded-resources > tbody > tr > td > '
            'a[title="Delete image"]')
        self.assertEqual(1, len(delete_btn))

    def test_hides_delete_button_for_uploaded_resource_when_not_admin(self):
        self.client_log_in()
        self.make_uploaded_resource()
        response = self.client.get(reverse('images'))
        doc = fromstring(response.content)
        delete_btn = doc.cssselect(
            'table#uploaded-resources > tbody > tr > td > '
            'a[title="Delete image"]')
        self.assertEqual(0, len(delete_btn))


class TestImageAjax(MAASServerTestCase):

    def get_images_ajax(self):
        return self.client.get(
            reverse('images'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def test__returns_json(self):
        self.client_log_in()
        response = self.get_images_ajax()
        self.assertEqual('application/json', response['Content-Type'])

    def test__returns_region_import_running_True(self):
        self.client_log_in()
        self.patch(
            images_view, 'is_import_resources_running').return_value = True
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertTrue(json_obj['region_import_running'])

    def test__returns_region_import_running_False(self):
        self.client_log_in()
        self.patch(
            images_view, 'is_import_resources_running').return_value = False
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertFalse(json_obj['region_import_running'])

    def test__returns_cluster_import_running_True(self):
        self.client_log_in()
        self.patch(
            images_view, 'is_import_boot_images_running').return_value = True
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertTrue(json_obj['cluster_import_running'])

    def test__returns_cluster_import_running_False(self):
        self.client_log_in()
        self.patch(
            images_view, 'is_import_boot_images_running').return_value = False
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertFalse(json_obj['cluster_import_running'])

    def test_returns_resources(self):
        self.client_log_in()
        resources = [factory.make_usable_boot_resource() for _ in range(3)]
        resource_ids = [resource.id for resource in resources]
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_ids = [
            json_resource['id']
            for json_resource in json_obj['resources']
            ]
        self.assertItemsEqual(resource_ids, json_ids)

    def test_returns_resources_datetime_format(self):
        """Ensure the date/time format is correct"""
        self.client_log_in()
        resource = factory.make_usable_boot_resource()
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_updated = datetime.datetime.strptime(
            json_obj['resources'][0]['lastUpdate'], "%a, %d %b. %Y %H:%M:%S")
        self.assertEqual(resource.updated.timetuple(),
                         json_updated.timetuple())

    def test_returns_resource_attributes(self):
        self.client_log_in()
        factory.make_usable_boot_resource()
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertThat(
            json_resource,
            ContainsAll([
                'id', 'rtype', 'name', 'title', 'arch', 'size',
                'complete', 'status', 'downloading',
                'numberOfNodes', 'lastUpdate']))

    def test_returns_ubuntu_release_version_name(self):
        self.client_log_in()
        # Use trusty as known to map to "14.04 LTS"
        version = '14.04 LTS'
        name = 'ubuntu/trusty'
        factory.make_usable_boot_resource(
            rtype=BOOT_RESOURCE_TYPE.SYNCED, name=name)
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual(version, json_resource['title'])

    def test_shows_number_of_nodes_deployed_for_resource(self):
        self.client_log_in()
        resource = factory.make_usable_boot_resource(
            rtype=BOOT_RESOURCE_TYPE.SYNCED)
        os_name, series = resource.name.split('/')
        number_of_nodes = random.randint(1, 4)
        for _ in range(number_of_nodes):
            factory.make_Node(
                status=NODE_STATUS.DEPLOYED,
                osystem=os_name, distro_series=series,
                architecture=resource.architecture)
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual(number_of_nodes, json_resource['numberOfNodes'])

    def test_shows_number_of_nodes_deployed_for_resource_with_defaults(self):
        self.client_log_in()
        resource = factory.make_usable_boot_resource(
            rtype=BOOT_RESOURCE_TYPE.SYNCED)
        os_name, series = resource.name.split('/')
        Config.objects.set_config('default_osystem', os_name)
        Config.objects.set_config('default_distro_series', series)
        number_of_nodes = random.randint(1, 4)
        for _ in range(number_of_nodes):
            factory.make_Node(
                status=NODE_STATUS.DEPLOYED,
                architecture=resource.architecture)
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual(number_of_nodes, json_resource['numberOfNodes'])

    def test_shows_number_of_nodes_deployed_for_ubuntu_subarch_resource(self):
        self.client_log_in()
        resource = factory.make_usable_boot_resource(
            rtype=BOOT_RESOURCE_TYPE.SYNCED)
        arch, subarch = resource.split_arch()
        extra_subarch = factory.make_name('subarch')
        resource.extra['subarches'] = ','.join([subarch, extra_subarch])
        resource.save()

        os_name, series = resource.name.split('/')
        node_architecture = '%s/%s' % (arch, extra_subarch)
        number_of_nodes = random.randint(1, 4)
        for _ in range(number_of_nodes):
            factory.make_Node(
                status=NODE_STATUS.DEPLOYED,
                osystem=os_name, distro_series=series,
                architecture=node_architecture)
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual(number_of_nodes, json_resource['numberOfNodes'])

    def test_combines_subarch_resources_into_one_resource(self):
        self.client_log_in()
        name = 'ubuntu/%s' % factory.make_name('series')
        arch = factory.make_name('arch')
        subarches = [factory.make_name('subarch') for _ in range(3)]
        for subarch in subarches:
            factory.make_usable_boot_resource(
                rtype=BOOT_RESOURCE_TYPE.SYNCED,
                name=name, architecture='%s/%s' % (arch, subarch))
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        self.assertEqual(
            1, len(json_obj['resources']),
            'More than one resource was returned.')

    def test_combined_subarch_resource_calculates_unique_size(self):
        self.client_log_in()
        name = 'ubuntu/%s' % factory.make_name('series')
        arch = factory.make_name('arch')
        subarches = [factory.make_name('subarch') for _ in range(3)]
        largefile = factory.make_LargeFile()
        for subarch in subarches:
            resource = factory.make_BootResource(
                rtype=BOOT_RESOURCE_TYPE.SYNCED,
                name=name, architecture='%s/%s' % (arch, subarch))
            resource_set = factory.make_BootResourceSet(resource)
            factory.make_BootResourceFile(resource_set, largefile)
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual(
            human_readable_bytes(largefile.total_size), json_resource['size'])

    def test_combined_subarch_resource_calculates_num_of_nodes_deployed(self):
        self.client_log_in()
        osystem = 'ubuntu'
        series = factory.make_name('series')
        name = '%s/%s' % (osystem, series)
        arch = factory.make_name('arch')
        subarches = [factory.make_name('subarch') for _ in range(3)]
        for subarch in subarches:
            factory.make_usable_boot_resource(
                rtype=BOOT_RESOURCE_TYPE.SYNCED,
                name=name, architecture='%s/%s' % (arch, subarch))

        number_of_nodes = random.randint(1, 4)
        for _ in range(number_of_nodes):
            subarch = random.choice(subarches)
            node_architecture = '%s/%s' % (arch, subarch)
            factory.make_Node(
                status=NODE_STATUS.DEPLOYED,
                osystem=osystem, distro_series=series,
                architecture=node_architecture)

        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual(number_of_nodes, json_resource['numberOfNodes'])

    def test_combined_subarch_resource_calculates_complete_True(self):
        self.client_log_in()
        name = 'ubuntu/%s' % factory.make_name('series')
        arch = factory.make_name('arch')
        subarches = [factory.make_name('subarch') for _ in range(3)]
        resources = [
            factory.make_usable_boot_resource(
                rtype=BOOT_RESOURCE_TYPE.SYNCED,
                name=name, architecture='%s/%s' % (arch, subarch))
            for subarch in subarches
            ]
        self.patch(
            BootResource.objects,
            'get_resources_matching_boot_images').return_value = resources
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertTrue(json_resource['complete'])

    def test_combined_subarch_resource_calculates_complete_False(self):
        self.client_log_in()
        name = 'ubuntu/%s' % factory.make_name('series')
        arch = factory.make_name('arch')
        subarches = [factory.make_name('subarch') for _ in range(3)]
        incomplete_subarch = subarches.pop()
        factory.make_BootResource(
            rtype=BOOT_RESOURCE_TYPE.SYNCED,
            name=name, architecture='%s/%s' % (arch, incomplete_subarch))
        for subarch in subarches:
            factory.make_usable_boot_resource(
                rtype=BOOT_RESOURCE_TYPE.SYNCED,
                name=name, architecture='%s/%s' % (arch, subarch))
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertFalse(json_resource['complete'])

    def test_combined_subarch_resource_calculates_progress(self):
        self.client_log_in()
        name = 'ubuntu/%s' % factory.make_name('series')
        arch = factory.make_name('arch')
        subarches = [factory.make_name('subarch') for _ in range(3)]
        largefile = factory.make_LargeFile()
        largefile.total_size = largefile.total_size * 2
        largefile.save()
        for subarch in subarches:
            resource = factory.make_BootResource(
                rtype=BOOT_RESOURCE_TYPE.SYNCED,
                name=name, architecture='%s/%s' % (arch, subarch))
            resource_set = factory.make_BootResourceSet(resource)
            factory.make_BootResourceFile(resource_set, largefile)
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual("Downloading  50%", json_resource['status'])

    def test_combined_subarch_resource_shows_queued_if_no_progress(self):
        self.client_log_in()
        name = 'ubuntu/%s' % factory.make_name('series')
        arch = factory.make_name('arch')
        subarches = [factory.make_name('subarch') for _ in range(3)]
        largefile = factory.make_LargeFile(
            content="".encode(settings.DEFAULT_CHARSET))
        for subarch in subarches:
            resource = factory.make_BootResource(
                rtype=BOOT_RESOURCE_TYPE.SYNCED,
                name=name, architecture='%s/%s' % (arch, subarch))
            resource_set = factory.make_BootResourceSet(resource)
            factory.make_BootResourceFile(resource_set, largefile)
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual("Queued for download", json_resource['status'])

    def test_combined_subarch_resource_shows_complete_status(self):
        self.client_log_in()
        name = 'ubuntu/%s' % factory.make_name('series')
        arch = factory.make_name('arch')
        subarches = [factory.make_name('subarch') for _ in range(3)]
        resources = [
            factory.make_usable_boot_resource(
                rtype=BOOT_RESOURCE_TYPE.SYNCED,
                name=name, architecture='%s/%s' % (arch, subarch))
            for subarch in subarches
            ]
        self.patch(
            BootResource.objects,
            'get_resources_matching_boot_images').return_value = resources
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual("Complete", json_resource['status'])

    def test_combined_subarch_resource_shows_waiting_for_cluster_to_sync(self):
        self.client_log_in()
        name = 'ubuntu/%s' % factory.make_name('series')
        arch = factory.make_name('arch')
        subarches = [factory.make_name('subarch') for _ in range(3)]
        for subarch in subarches:
            factory.make_usable_boot_resource(
                rtype=BOOT_RESOURCE_TYPE.SYNCED,
                name=name, architecture='%s/%s' % (arch, subarch))
        self.patch(
            BootResource.objects,
            'get_resources_matching_boot_images').return_value = []
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual(
            "Waiting for rack controller(s) to sync", json_resource['status'])

    def test_combined_subarch_resource_shows_clusters_syncing(self):
        self.client_log_in()
        name = 'ubuntu/%s' % factory.make_name('series')
        arch = factory.make_name('arch')
        subarches = [factory.make_name('subarch') for _ in range(3)]
        for subarch in subarches:
            factory.make_usable_boot_resource(
                rtype=BOOT_RESOURCE_TYPE.SYNCED,
                name=name, architecture='%s/%s' % (arch, subarch))
        self.patch(
            BootResource.objects,
            'get_resources_matching_boot_images').return_value = []
        self.patch(
            images_view, 'is_import_boot_images_running').return_value = True
        response = self.get_images_ajax()
        json_obj = json.loads(
            response.content.decode(settings.DEFAULT_CHARSET))
        json_resource = json_obj['resources'][0]
        self.assertEqual(
            "Syncing to rack controller(s)", json_resource['status'])


class TestImageDelete(MAASServerTestCase):

    def test_non_admin_cannot_delete(self):
        self.client_log_in()
        resource = factory.make_BootResource(rtype=BOOT_RESOURCE_TYPE.UPLOADED)
        response = self.client.post(
            reverse('image-delete', args=[resource.id]))
        self.assertEqual(http.client.FORBIDDEN, response.status_code)
        self.assertIsNotNone(reload_object(resource))

    def test_deletes_resource(self):
        self.client_log_in(as_admin=True)
        resource = factory.make_BootResource(rtype=BOOT_RESOURCE_TYPE.UPLOADED)
        response = self.client.post(
            reverse('image-delete', args=[resource.id]),
            {'post': 'yes'})
        self.assertEqual(http.client.FOUND, response.status_code)
        self.assertIsNone(reload_object(resource))

    def test_redirects_to_images(self):
        self.client_log_in(as_admin=True)
        resource = factory.make_BootResource(rtype=BOOT_RESOURCE_TYPE.UPLOADED)
        response = self.client.post(
            reverse('image-delete', args=[resource.id]),
            {'post': 'yes'})
        self.assertEqual(reverse('images'), extract_redirect(response))
