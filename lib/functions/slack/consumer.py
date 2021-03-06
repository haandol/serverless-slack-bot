import os
import sys
import json
import boto3
import logging
import requests
import traceback
from importlib import import_module
sys.path.append(os.path.abspath('.'))
logger = logging.getLogger('consumter')
logger.setLevel(logging.INFO)

sqs = boto3.client('sqs')
ssm = boto3.client('ssm')

ACCESS_TOKEN_KEY = os.environ['ACCESS_TOKEN_KEY']
QUEUE_URL = os.environ['QUEUE_URL']
APPS = json.loads(os.environ['APPS'])
CMD_PREFIX = os.environ['CMD_PREFIX']
CMD_LENGTH = len(CMD_PREFIX)

robot = None


class Robot(object):
    def __init__(self):
        self.access_token = None
        self.queue_url = QUEUE_URL
        self.apps, self.docs = self.load_apps()
        self.logger = logger
        self.post_url = 'https://slack.com/api/chat.postMessage'
        self.brain = Brain(ssm)

    def get_access_token(self):
        if not self.access_token:
            try:
                resp = ssm.get_parameter(Name=ACCESS_TOKEN_KEY, WithDecryption=True)
                self.access_token = resp['Parameter']['Value']
            except:
                self.logger.error(traceback.format_exc())
        return self.access_token

    def load_apps(self):
        docs = ['='*14, 'Usage', '='*14]
        apps = {}

        for name in APPS:
            app = import_module('apps.%s' % name)
            cmd_list_str = '|'.join(app.run.commands)
            docs.append(f'{CMD_PREFIX}[{cmd_list_str}]: {app.run.__doc__}')
                
            for command in app.run.commands:
                apps[command] = app

        return apps, docs

    def handle_data(self, data):
        channel, user, text = data

        command, payloads = self.extract_command(text)
        if not command:
            return

        app = self.apps.get(command, None)
        if not app:
            return

        try:
            app.run(self, channel, user, payloads)
        except:
            self.logger.error(traceback.format_exc())

    def extract_command(self, text):
        if CMD_PREFIX and CMD_PREFIX != text[0]:
            return (None, None)

        tokens = text.split(' ', 1)
        if 1 < len(tokens):
            return tokens[0][CMD_LENGTH:], tokens[1]
        else:
            return (text[CMD_LENGTH:], '')

    def post_message(self, channel, message):
        resp = requests.post(url=self.post_url, data={
            'token': self.get_access_token(),
            'channel': channel,
            'text': message,
        }, timeout=3).json()
        if 'error' in resp and 'invalid_auth' == resp['error']:
            self.access_token = None
            requests.post(url=self.post_url, data={
                'token': self.get_access_token(),
                'channel': channel,
                'text': message,
            }, timeout=3)


class Brain(object):
    def __init__(self, ssm):
        self.ssm = ssm

    def store(self, key, value):
        self.ssm.put_parameter(Name=key, Value=value, Type='String')
    
    def get(self, key):
        try:
            return self.ssm.get_parameter(Name=key)['Parameter']['Value']
        except ssm.exceptions.ParameterNotFound:
            return ''

    def get_list(self, path, max_results=10):
        return self.ssm.get_parameters_by_path(
            Path=path, Recursive=True, MaxResults=max_results
        )['Parameters']


def handler(event, context):
    logger.info(event)

    global robot
    if not robot:
        robot = Robot()

    for record in event['Records']:
        receipt_handler = record['receiptHandle']
        body = json.loads(record['body'])
        data = (body['channel'], body['user'], body['text'])
        try:
            robot.handle_data(data)
        except:
            traceback.print_exc()
        else:
            sqs.delete_message(
                QueueUrl=QUEUE_URL,
                ReceiptHandle=receipt_handler
            )