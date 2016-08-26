# Copyright 2012-2016 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Generate commissioning user-data from template and code snippets.

This combines the snippets of code in the `snippets` directory into
the main commissioning script.

Its contents are not customizable.  To inject custom code, use the
:class:`CommissioningScript` model.
"""

__all__ = [
    'generate_user_data',
    ]

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import os.path

from maasserver.preseed import get_preseed_context
from metadataserver.user_data.snippets import get_snippet_context
import tempita


ENCODING = 'utf-8'


def generate_user_data(node, userdata_dir,
                       userdata_template_name, extra_context=None):
    """Produce a user_data script for use by commissioning and other
    operations.

    The main template file contains references to so-called ``snippets''
    which are read in here, and substituted.  In addition, the regular
    preseed context variables are available (such as 'http_proxy').

    The final result is a MIME multipart message that consists of a
    'cloud-config' part and an 'x-shellscript' part.  This allows maximum
    flexibility with cloud-init as we read in a template
    'user_data_config.template' to set cloud-init configs before the script
    is run.

    :rtype: `bytes`
    """
    userdata_template_file = os.path.join(
        userdata_dir, userdata_template_name)
    userdata_template = tempita.Template.from_filename(
        userdata_template_file, encoding=ENCODING)
    # The preseed context is a dict containing various configs that the
    # templates can use.
    preseed_context = get_preseed_context(
        rack_controller=node.get_boot_rack_controller())
    preseed_context['node'] = node

    # Render the snippets in the main template.
    snippets = get_snippet_context(encoding=ENCODING)
    snippets.update(preseed_context)
    if extra_context is not None:
        snippets.update(extra_context)
    userdata = userdata_template.substitute(snippets).encode(ENCODING)

    data_part = MIMEText(userdata, 'x-shellscript', ENCODING)
    data_part.add_header(
        'Content-Disposition', 'attachment; filename="user_data.sh"')
    combined = MIMEMultipart()
    combined.attach(data_part)
    return combined.as_bytes()
