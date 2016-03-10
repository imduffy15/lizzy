from unittest.mock import MagicMock, ANY
import json
import os
import pytest
import requests

import lizzy.api
from lizzy.models.stack import Stack
from lizzy.exceptions import (ObjectNotFound, AIMImageNotUpdated,
                              TrafficNotUpdated)
from lizzy.service import setup_webapp

CURRENT_VERSION = '2016-02-22'

GOOD_HEADERS = {'Authorization': 'Bearer 100', 'Content-type': 'application/json'}

STACKS = {'stack1': {'stack_id': None,
                     'creation_time': None,
                     'keep_stacks': 1,
                     'traffic': 100,
                     'image_version': 'version',
                     'aim_image': 'latest',
                     'senza_yaml': 'yaml',
                     'stack_name': 'stackno1',
                     'stack_version': 'v1',
                     'status': 'LIZZY:NEW', }}


class FakeConfig:
    def __init__(self):
        self.deployer_scope = 'myscope'
        self.token_url = 'https://ouath.example/access_token'
        self.port = 8080


class FakeResponse:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text
        self.ok = status_code == 200

    def json(self):
        return json.loads(self.text)


class FakeRequest:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text
        self.ok = status_code == 200

    def json(self):
        return json.loads(self.text)


class FakeStack(Stack):
    # TODO Implement some stacks

    last_save = {}

    @classmethod
    def all(cls):
        return []

    def delete(self):
        pass

    @classmethod
    def get(cls, uid):
        if uid not in STACKS:
            raise ObjectNotFound(uid)

        stack = STACKS[uid]
        return cls(**stack)

    def save(self):
        FakeStack.last_save = vars(self)


@pytest.fixture
def app(monkeypatch):
    os.environ['TOKENINFO_URL'] = 'https://ouath.example/token_info'
    app = setup_webapp(FakeConfig())
    app_client = app.app.test_client()

    monkeypatch.setattr(lizzy.api, 'Stack', FakeStack)

    return app_client


@pytest.fixture(autouse=True)
def oauth_requests(monkeypatch: '_pytest.monkeypatch.monkeypatch'):
    class Session(requests.Session):
        def get(self, url: str, params: dict=None, timeout=0):
            params = params or {}
            if url == "https://ouath.example/token_info":
                token = params['access_token']
                if token == "100":
                    return FakeResponse(200, '{"scope": ["myscope"], "uid": ["test_user"]}')
                if token == "200":
                    return FakeResponse(200, '{"scope": ["wrongscope"], , "uid": ["test_user"]}')
                if token == "300":
                    return FakeResponse(404, '')
            return url

    monkeypatch.setattr('connexion.decorators.security.session', Session())


def test_security(app, oauth_requests):
    get_swagger = app.get('/api/swagger.json')  # type:flask.Response
    assert get_swagger.status_code == 200

    get_stacks_no_auth = app.get('/api/stacks')  # type:flask.Response
    assert get_stacks_no_auth.status_code == 401

    get_stacks = app.get('/api/stacks', headers=GOOD_HEADERS)
    assert get_stacks.status_code == 200
    assert get_stacks.headers['X-Lizzy-Version'] == CURRENT_VERSION


def test_empty_new_stack(monkeypatch, app, oauth_requests):
    data = {}
    request = app.post('/api/stacks', headers=GOOD_HEADERS, data=json.dumps(data))  # type: flask.Response
    assert request.status_code == 400
    response = json.loads(request.data.decode())
    assert response['title'] == 'Bad Request'


def test_bad_senza_yaml(app, oauth_requests):
    data = {'keep_stacks': 0,
            'new_traffic': 100,
            'image_version': '1.0',
            'senza_yaml': 'key: value'}

    request = app.post('/api/stacks', headers=GOOD_HEADERS, data=json.dumps(data))  # type: flask.Response
    assert request.status_code == 400
    response = json.loads(request.data.decode())
    assert response['title'] == 'Invalid senza yaml'
    assert response['detail'] == "Missing property in senza yaml: 'SenzaInfo'"
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION

    data['senza_yaml'] = '[]'
    request = app.post('/api/stacks', headers=GOOD_HEADERS, data=json.dumps(data))  # type: flask.Response
    assert request.status_code == 400
    response = json.loads(request.data.decode())
    assert response['title'] == 'Invalid senza yaml'
    assert response['detail'] == "Senza yaml is not a dict."
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION


def test_new_stack(monkeypatch, app, oauth_requests):
    data = {'keep_stacks': 0,
            'new_traffic': 100,
            'image_version': '1.0',
            'senza_yaml': 'SenzaInfo:\n  StackName: abc'}

    mock_senza = MagicMock()
    mock_senza.return_value = mock_senza
    monkeypatch.setattr('lizzy.api.Senza', mock_senza)
    mock_kio = MagicMock()
    mock_kio.return_value = mock_kio
    monkeypatch.setattr('lizzy.api.Kio', mock_kio)

    mock_senza.create.return_value = True
    request = app.post('/api/stacks', headers=GOOD_HEADERS, data=json.dumps(data))  # type: flask.Response
    mock_kio.assert_not_called()
    mock_senza.assert_called_with('eu-west-1')
    mock_senza.create.assert_called_with('SenzaInfo:\n  StackName: abc', ANY, '1.0', [], False)
    assert request.status_code == 201
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION
    stack_version = FakeStack.last_save['stack_version']
    assert FakeStack.last_save['application_version'] is None
    assert FakeStack.last_save['image_version'] == '1.0'
    assert FakeStack.last_save['keep_stacks'] == 0
    assert FakeStack.last_save['parameters'] == []
    assert FakeStack.last_save['stack_id'] == 'abc-' + stack_version
    assert FakeStack.last_save['stack_name'] == 'abc'
    assert FakeStack.last_save['status'] == 'LIZZY:DEPLOYING'
    assert FakeStack.last_save['traffic'] == 100

    data = {'keep_stacks': 0,
            'new_traffic': 100,
            'image_version': '1.0',
            'senza_yaml': 'SenzaInfo:\n  StackName: abc',
            'parameters': ['abc', 'def']}

    request = app.post('/api/stacks', headers=GOOD_HEADERS, data=json.dumps(data))  # type: flask.Response
    mock_kio.assert_not_called()
    mock_senza.assert_called_with('eu-west-1')
    mock_senza.create.assert_called_with('SenzaInfo:\n  StackName: abc', ANY, '1.0', ['abc', 'def'], False)
    assert request.status_code == 201
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION
    stack_version = FakeStack.last_save['stack_version']
    assert FakeStack.last_save['application_version'] is None
    assert FakeStack.last_save['image_version'] == '1.0'
    assert FakeStack.last_save['keep_stacks'] == 0
    assert FakeStack.last_save['parameters'] == ['abc', 'def']
    assert FakeStack.last_save['stack_id'] == 'abc-' + stack_version
    assert FakeStack.last_save['stack_name'] == 'abc'
    assert FakeStack.last_save['status'] == 'LIZZY:DEPLOYING'
    assert FakeStack.last_save['traffic'] == 100

    data = {'keep_stacks': 0,
            'new_traffic': 100,
            'image_version': '1.0',
            'senza_yaml': 'SenzaInfo:\n  StackName: abc',
            'parameters': ['abc', 'def'],
            'application_version': '42'}
    mock_senza.reset_mock()
    mock_kio.versions_create.return_value = True
    request = app.post('/api/stacks', headers=GOOD_HEADERS, data=json.dumps(data))  # type: flask.Response
    mock_senza.assert_called_with('eu-west-1')
    mock_senza.create.assert_called_with('SenzaInfo:\n  StackName: abc', ANY, '1.0', ['abc', 'def'], False)
    mock_kio.assert_called_with()
    mock_kio.versions_create.assert_called_once_with('abc', '42', '')
    assert request.status_code == 201
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION
    assert FakeStack.last_save['application_version'] == '42'
    assert FakeStack.last_save['image_version'] == '1.0'
    assert FakeStack.last_save['keep_stacks'] == 0
    assert FakeStack.last_save['parameters'] == ['abc', 'def']
    assert FakeStack.last_save['stack_id'] == 'abc-42'
    assert FakeStack.last_save['stack_name'] == 'abc'
    assert FakeStack.last_save['status'] == 'LIZZY:DEPLOYING'
    assert FakeStack.last_save['traffic'] == 100

    mock_kio.reset_mock()
    mock_senza.reset_mock()
    data = {'keep_stacks': 0,
            'new_traffic': 100,
            'image_version': '1.0',
            'senza_yaml': 'SenzaInfo:\n  StackName: abc',
            'parameters': ['abc', 'def'],
            'application_version': '42',
            'disable_rollback': True}

    mock_kio.versions_create.return_value = True
    request = app.post('/api/stacks', headers=GOOD_HEADERS, data=json.dumps(data))  # type: flask.Response
    mock_senza.assert_called_with('eu-west-1')
    mock_senza.create.assert_called_with('SenzaInfo:\n  StackName: abc', ANY, '1.0', ['abc', 'def'], True)
    mock_kio.assert_called_with()
    mock_kio.versions_create.assert_called_once_with('abc', '42', '')
    assert request.status_code == 201
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION
    assert FakeStack.last_save['application_version'] == '42'
    assert FakeStack.last_save['image_version'] == '1.0'
    assert FakeStack.last_save['keep_stacks'] == 0
    assert FakeStack.last_save['parameters'] == ['abc', 'def']
    assert FakeStack.last_save['stack_id'] == 'abc-42'
    assert FakeStack.last_save['stack_name'] == 'abc'
    assert FakeStack.last_save['status'] == 'LIZZY:DEPLOYING'
    assert FakeStack.last_save['traffic'] == 100

    # kio version creation doesn't affect the rest of the process
    mock_kio.versions_create.reset_mock()
    mock_kio.versions_create.return_value = False
    request = app.post('/api/stacks', headers=GOOD_HEADERS, data=json.dumps(data))  # type: flask.Response
    mock_kio.assert_called_with()
    mock_kio.versions_create.assert_called_once_with('abc', '42', '')
    assert request.status_code == 201
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION
    assert FakeStack.last_save['application_version'] == '42'
    assert FakeStack.last_save['image_version'] == '1.0'
    assert FakeStack.last_save['keep_stacks'] == 0
    assert FakeStack.last_save['parameters'] == ['abc', 'def']
    assert FakeStack.last_save['stack_id'] == 'abc-42'
    assert FakeStack.last_save['stack_name'] == 'abc'
    assert FakeStack.last_save['status'] == 'LIZZY:DEPLOYING'
    assert FakeStack.last_save['traffic'] == 100

    mock_senza.create.return_value = False
    request = app.post('/api/stacks', headers=GOOD_HEADERS, data=json.dumps(data))  # type: flask.Response
    assert request.status_code == 400


def test_invalid_yaml(app, oauth_requests):
    data = {'keep_stacks': 0,
            'new_traffic': 100,
            'image_version': '1.0',
            'senza_yaml': '*invalid*yaml*file'}

    request = app.post('/api/stacks', headers=GOOD_HEADERS, data=json.dumps(data))  # type: flask.Response
    assert request.status_code == 400
    response = json.loads(request.data.decode())  # type: dict
    assert response['title'] == 'Invalid senza yaml'
    assert response['detail'] == "Failed to parse senza yaml."
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION


def test_get_stack(app, oauth_requests):
    parameters = ['stack_version', 'stack_name', 'senza_yaml', 'creation_time', 'image_version', 'status', 'stack_id']

    request = app.get('/api/stacks/stack1', headers=GOOD_HEADERS)
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION
    response = json.loads(request.data.decode())  # type: dict
    keys = response.keys()
    for parameter in parameters:
        assert parameter in keys


def test_get_stack_404(app, oauth_requests):
    request = app.get('/api/stacks/stack404', headers=GOOD_HEADERS)
    assert request.status_code == 404
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION


def test_delete(app, oauth_requests):
    request = app.delete('/api/stacks/stack1', headers=GOOD_HEADERS)
    assert request.status_code == 204
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION

    # delete is idempotent
    request = app.delete('/api/stacks/stack1', headers=GOOD_HEADERS)
    assert request.status_code == 204
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION

    request = app.delete('/api/stacks/stack404', headers=GOOD_HEADERS)
    assert request.status_code == 204
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION


def test_patch(monkeypatch, app, oauth_requests):
    mock_deployer = MagicMock()
    mock_deployer.return_value = mock_deployer
    monkeypatch.setattr('lizzy.api.InstantDeployer', mock_deployer)

    data = {'new_traffic': 50}

    # Only changes the traffic
    request = app.patch('/api/stacks/stack1', headers=GOOD_HEADERS,
                        data=json.dumps(data))
    assert request.status_code == 202
    assert FakeStack.last_save['traffic'] == 50
    mock_deployer.change_traffic.assert_called_once_with(50)
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION

    # Should return 400 when not possible to change the traffic
    mock_deployer.change_traffic.side_effect = TrafficNotUpdated(
        'fake error')
    request = app.patch('/api/stacks/stack1', headers=GOOD_HEADERS,
                        data=json.dumps(data))
    assert request.status_code == 400

    # Does not change anything
    request = app.patch('/api/stacks/stack1', headers=GOOD_HEADERS,
                        data=json.dumps({}))
    assert request.status_code == 202
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION

    update_image = {'new_aim_image': 'aim-2323'}

    # Should change the AIM image used by the stack and respawnn the instances
    # using the new AIM image.
    request = app.patch('/api/stacks/stack1', headers=GOOD_HEADERS, data=json.dumps(update_image))
    assert request.status_code == 202
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION
    assert FakeStack.last_save['aim_image'] == 'aim-2323'
    mock_deployer.update_aim_image.assert_called_once_with('aim-2323')

    # Should return 400 when not possible to change the AIM image
    mock_deployer.update_aim_image.side_effect = AIMImageNotUpdated('fake error')
    request = app.patch('/api/stacks/stack1', headers=GOOD_HEADERS, data=json.dumps(update_image))
    assert request.status_code == 400


def test_patch404(app, oauth_requests):
    data = {'new_traffic': 50, }

    request = app.patch('/api/stacks/stack404', headers=GOOD_HEADERS, data=json.dumps(data))
    assert request.status_code == 404
    assert request.headers['X-Lizzy-Version'] == CURRENT_VERSION
