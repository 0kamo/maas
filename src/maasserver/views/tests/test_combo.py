# Copyright 2012-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Test combo view."""

__all__ = []

from collections import Callable
import http.client
import os

from django.conf import settings
from django.core.urlresolvers import reverse
from django.test.client import RequestFactory
from maasserver.testing import extract_redirect
from maasserver.testing.factory import factory
from maasserver.testing.testcase import MAASServerTestCase
from maasserver.views.combo import (
    get_absolute_location,
    get_combo_view,
    MERGE_VIEWS,
)
from maastesting.fixtures import ImportErrorFixture


class TestUtilities(MAASServerTestCase):

    def test_get_abs_location_returns_absolute_location_if_not_None(self):
        abs_location = '%s%s' % (os.path.sep, factory.make_string())
        self.assertEqual(
            abs_location, get_absolute_location(location=abs_location))

    def test_get_abs_location_returns_rel_loc_if_not_in_dev_environment(self):
        self.useFixture(ImportErrorFixture('maastesting', 'root'))
        static_root = factory.make_string()
        self.patch(settings, 'STATIC_ROOT', static_root)
        rel_location = os.path.join(
            factory.make_string(), factory.make_string())
        expected_location = os.path.join(static_root, rel_location)
        observed = get_absolute_location(location=rel_location)
        self.assertEqual(expected_location, observed)

    def test_get_abs_location_returns_rel_loc_if_in_dev_environment(self):
        rel_location = os.path.join(
            factory.make_string(), factory.make_string())
        rel_location_base = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'static')
        expected_location = os.path.join(rel_location_base, rel_location)
        self.assertEqual(
            expected_location, get_absolute_location(location=rel_location))

    def test_get_combo_view_returns_callable(self):
        rel_location = os.path.join(
            factory.make_string(), factory.make_string())
        view = get_combo_view(rel_location)
        self.assertIsInstance(view, Callable)

    def test_get_combo_view_loads_from_disk(self):
        test_file_contents = factory.make_string()
        # Create a valid file with a proper extension (the combo loader only
        # serves JS or CSS files)
        test_file_name = "%s.js" % factory.make_string()
        test_file = self.make_file(
            name=test_file_name, contents=test_file_contents)
        directory = os.path.dirname(test_file)
        view = get_combo_view(directory)
        # Create a request for test file.
        rf = RequestFactory()
        request = rf.get("/test/?%s" % test_file_name)
        response = view(request)
        expected_content = '/* %s */\n%s\n' % (
            test_file_name, test_file_contents)
        self.assertEqual(
            (http.client.OK, expected_content.encode(
                settings.DEFAULT_CHARSET)),
            (response.status_code, response.content))

    def test_get_combo_redirects_if_unknown_type(self):
        # The optional parameter 'default_redirect' allows to configure
        # a default address where requests for files of unknown types will be
        # redirected.
        # Create a test file with an unknown extension.
        test_file_name = "%s.%s" % (
            factory.make_string(), factory.make_string())
        redirect_root = factory.make_string()
        view = get_combo_view(
            factory.make_string(), default_redirect=redirect_root)
        rf = RequestFactory()
        request = rf.get("/test/?%s" % test_file_name)
        response = view(request)
        self.assertEqual(
            '%s%s' % (redirect_root, test_file_name),
            extract_redirect(response))


# String used by convoy to replace missing files.
CONVOY_MISSING_FILE = b"/* [missing] */"


class TestComboLoaderView(MAASServerTestCase):
    """Test combo loader views."""

    def test_yui_load_js(self):
        requested_files = [
            'oop/oop.js',
            'event-custom-base/event-custom-base.js'
            ]
        url = '%s?%s' % (reverse('combo-yui'), '&'.join(requested_files))
        response = self.client.get(url)
        self.assertIn('text/javascript', response['Content-Type'])
        for requested_file in requested_files:
            self.assertIn(
                requested_file.encode(settings.DEFAULT_CHARSET),
                response.content)
        # No sign of a missing js file.
        self.assertNotIn(CONVOY_MISSING_FILE, response.content)
        # The file contains a link to YUI's licence.
        self.assertIn(b'http://yuilibrary.com/license/', response.content)

    def test_yui_load_css(self):
        requested_files = [
            'widget-base/assets/skins/sam/widget-base.css',
            'widget-stack/assets/skins/sam/widget-stack.css',
            ]
        url = '%s?%s' % (reverse('combo-yui'), '&'.join(requested_files))
        response = self.client.get(url)
        self.assertIn('text/css', response['Content-Type'])
        for requested_file in requested_files:
            self.assertIn(
                requested_file.encode(settings.DEFAULT_CHARSET),
                response.content)
        # No sign of a missing css file.
        self.assertNotIn(CONVOY_MISSING_FILE, response.content)
        # The file contains a link to YUI's licence.
        self.assertIn(b'http://yuilibrary.com/license/', response.content)

    def test_yui_combo_no_file_returns_not_found(self):
        response = self.client.get(reverse('combo-yui'))
        self.assertEqual(http.client.NOT_FOUND, response.status_code)

    def test_yui_combo_other_file_extension_returns_bad_request(self):
        url = '%s?%s' % (reverse('combo-yui'), 'file.wrongextension')
        response = self.client.get(url)
        self.assertEqual(
            (http.client.BAD_REQUEST, b"Invalid file type requested."),
            (response.status_code, response.content))


class TestMergeLoaderView(MAASServerTestCase):
    """Test merge loader views."""

    def test_loads_all_views_correctly(self):
        for filename, merge_info in MERGE_VIEWS.items():
            url = reverse('merge', args=[filename])
            response = self.client.get(url)
            self.assertEqual(
                merge_info["content_type"], response['Content-Type'],
                "Content-type for %s does not match." % filename)

            # Has all required files.
            for requested_file in merge_info["files"]:
                self.assertIn(
                    requested_file,
                    response.content.decode(settings.DEFAULT_CHARSET))

            # No sign of a missing js file.
            self.assertNotIn(
                CONVOY_MISSING_FILE,
                response.content.decode(settings.DEFAULT_CHARSET))

    def test_load_unknown_returns_302_blocked_by_middleware(self):
        response = self.client.get(reverse('merge', args=["unknown.js"]))
        self.assertEqual(http.client.FOUND, response.status_code)
