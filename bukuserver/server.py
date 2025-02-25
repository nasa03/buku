#!/usr/bin/env python
# pylint: disable=wrong-import-order, ungrouped-imports
"""Server module."""
import os
import sys
from typing import Union  # NOQA; type: ignore
from urllib.parse import urlparse

from flask.cli import FlaskGroup
from flask_admin import Admin
from flask_api import FlaskAPI, status
from flask_bootstrap import Bootstrap

from buku import BukuDb, __version__, network_handler

try:
    from flask_reverse_proxy_fix.middleware import ReverseProxyPrefixFix
except ImportError:
    ReverseProxyPrefixFix = None
import click
import flask
from flask import __version__ as flask_version  # type: ignore
from flask import (
    current_app,
    jsonify,
    redirect,
    request,
    url_for,
)

try:
    from . import api, response, views
except ImportError:
    from bukuserver import api, response, views


STATISTIC_DATA = None

def handle_network():
    failed_resp = response.response_template['failure'], status.HTTP_400_BAD_REQUEST
    url = request.data.get('url', None)
    if not url:
        return failed_resp
    try:
        res = network_handler(url)
        keys = ['title', 'description', 'tags', 'recognized mime', 'bad url']
        res_dict = dict(zip(keys, res))
        return jsonify(res_dict)
    except Exception as e:
        current_app.logger.debug(str(e))
    return failed_resp


def refresh_bookmark(rec_id: Union[int, None]):
    result_flag = getattr(flask.g, 'bukudb', api.get_bukudb()).refreshdb(rec_id or 0, request.form.get('threads', 4))
    return api.to_response(result_flag)


def get_tiny_url(rec_id):
    url = getattr(flask.g, 'bukudb', api.get_bukudb()).tnyfy_url(rec_id)
    return jsonify({'url': url}) if url else api.response_bad()


_BOOL_VALUES = {'true': True, '1': True, 'false': False, '0': False}
def get_bool_from_env_var(key: str, default_value: bool) -> bool:
    """Get bool value from env var."""
    return _BOOL_VALUES.get(os.getenv(key, '').lower(), default_value)


def init_locale(app):
    try:
        from flask_babelex import Babel
        Babel(app).localeselector(lambda: app.config['BUKUSERVER_LOCALE'])
    except Exception:
        app.logger.warning('failed to init locale')


def create_app(db_file=None):
    """create app."""
    app = FlaskAPI(__name__)
    per_page = int(os.getenv('BUKUSERVER_PER_PAGE', str(views.DEFAULT_PER_PAGE)))
    per_page = per_page if per_page > 0 else views.DEFAULT_PER_PAGE
    app.config['BUKUSERVER_PER_PAGE'] = per_page
    url_render_mode = os.getenv('BUKUSERVER_URL_RENDER_MODE', views.DEFAULT_URL_RENDER_MODE)
    if url_render_mode not in ('full', 'netloc'):
        url_render_mode = views.DEFAULT_URL_RENDER_MODE
    app.config['BUKUSERVER_URL_RENDER_MODE'] = url_render_mode
    app.config['SECRET_KEY'] = os.getenv('BUKUSERVER_SECRET_KEY') or os.urandom(24)
    app.config['BUKUSERVER_READONLY'] = \
        get_bool_from_env_var('BUKUSERVER_READONLY', False)
    app.config['BUKUSERVER_DISABLE_FAVICON'] = \
        get_bool_from_env_var('BUKUSERVER_DISABLE_FAVICON', True)
    app.config['BUKUSERVER_OPEN_IN_NEW_TAB'] = \
        get_bool_from_env_var('BUKUSERVER_OPEN_IN_NEW_TAB', False)
    app.config['BUKUSERVER_DB_FILE'] = os.getenv('BUKUSERVER_DB_FILE') or db_file
    reverse_proxy_path = os.getenv('BUKUSERVER_REVERSE_PROXY_PATH')
    if reverse_proxy_path:
        if not reverse_proxy_path.startswith('/'):
            print('Warning: reverse proxy path should include preceding slash')
        if reverse_proxy_path.endswith('/'):
            print('Warning: reverse proxy path should not include trailing slash')
        app.config['REVERSE_PROXY_PATH'] = reverse_proxy_path
        if ReverseProxyPrefixFix:
            ReverseProxyPrefixFix(app)
        else:
            raise ImportError('Failed to import ReverseProxyPrefixFix')
    bukudb = BukuDb(dbfile=app.config['BUKUSERVER_DB_FILE'])
    app.config['FLASK_ADMIN_SWATCH'] = (os.getenv('BUKUSERVER_THEME') or 'default').lower()
    app.config['BUKUSERVER_LOCALE'] = os.getenv('BUKUSERVER_LOCALE') or 'en'
    app.app_context().push()
    setattr(flask.g, 'bukudb', bukudb)
    init_locale(app)

    @app.shell_context_processor
    def shell_context():
        """Shell context definition."""
        return {'app': app, 'bukudb': bukudb}

    app.jinja_env.filters['netloc'] = lambda x: urlparse(x).netloc  # pylint: disable=no-member

    Bootstrap(app)
    admin = Admin(
        app, name='buku server', template_mode='bootstrap3',
        index_view=views.CustomAdminIndexView(
            template='bukuserver/home.html', url='/'
        )
    )
    # routing
    #  api
    tag_api_view = api.ApiTagView.as_view('tag_api')
    app.add_url_rule('/api/tags', defaults={'tag': None}, view_func=tag_api_view, methods=['GET'])
    app.add_url_rule('/api/tags/<tag>', view_func=tag_api_view, methods=['GET', 'PUT'])
    bookmark_api_view = api.ApiBookmarkView.as_view('bookmark_api')
    app.add_url_rule('/api/bookmarks', defaults={'rec_id': None}, view_func=bookmark_api_view, methods=['GET', 'POST', 'DELETE'])
    app.add_url_rule('/api/bookmarks/<int:rec_id>', view_func=bookmark_api_view, methods=['GET', 'PUT', 'DELETE'])
    app.add_url_rule('/api/bookmarks/refresh', 'refresh_bookmark', refresh_bookmark, defaults={'rec_id': None}, methods=['POST'])
    app.add_url_rule('/api/bookmarks/<int:rec_id>/refresh', 'refresh_bookmark', refresh_bookmark, methods=['POST'])
    app.add_url_rule('/api/bookmarks/<int:rec_id>/tiny', 'get_tiny_url', get_tiny_url, methods=['GET'])
    app.add_url_rule('/api/network_handle', 'network_handle', handle_network, methods=['POST'])
    bookmark_range_api_view = api.ApiBookmarkRangeView.as_view('bookmark_range_api')
    app.add_url_rule(
        '/api/bookmarks/<int:starting_id>/<int:ending_id>',
        view_func=bookmark_range_api_view, methods=['GET', 'PUT', 'DELETE'])
    bookmark_search_api_view = api.ApiBookmarkSearchView.as_view('bookmark_search_api')
    app.add_url_rule('/api/bookmarks/search', view_func=bookmark_search_api_view, methods=['GET', 'DELETE'])
    bookmarklet_view = api.BookmarkletView.as_view('bookmarklet')
    app.add_url_rule('/bookmarklet', view_func=bookmarklet_view, methods=['GET'])

    #  non api
    @app.route('/favicon.ico')
    def favicon():
        return redirect(url_for('static', filename='bukuserver/favicon.svg'), code=301)  # permanent redirect

    admin.add_view(views.BookmarkModelView(
        bukudb, 'Bookmarks', page_size=per_page, url_render_mode=url_render_mode))
    admin.add_view(views.TagModelView(
        bukudb, 'Tags', page_size=per_page))
    admin.add_view(views.StatisticView(
        bukudb, 'Statistic', endpoint='statistic'))
    return app


class CustomFlaskGroup(FlaskGroup):  # pylint: disable=too-few-public-methods
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for idx, param in enumerate(self.params):
            if param.name == "version":
                self.params[idx].help = "Show the program version"
                self.params[idx].callback = get_custom_version


def get_custom_version(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return
    message = "\n".join(["%(app_name)s %(app_version)s", "Flask %(version)s", "Python %(python_version)s"])
    click.echo(
        message
        % {
            "app_name": "buku",
            "app_version": __version__,
            "version": flask_version,
            "python_version": sys.version,
        },
        color=ctx.color,
    )
    ctx.exit()


@click.group(cls=CustomFlaskGroup, create_app=create_app)
def cli():
    """This is a script for the bukuserver application."""


if __name__ == '__main__':
    cli()
