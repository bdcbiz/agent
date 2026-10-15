from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from peewee import SqliteDatabase

from agent.job import Job, JobModel, job


class ExampleJob:
    def run(self):
        pass


class EnqueueExample:
    def __init__(self):
        self.job_record = Job()

    @job("Example", timeout=1)
    def run(self, registry):
        return registry


class TestJobDataRedaction(unittest.TestCase):
    def setUp(self):
        self.database = SqliteDatabase(":memory:")
        self.database.bind([JobModel])
        self.database.connect()
        self.database.create_tables([JobModel])

    def tearDown(self):
        self.database.drop_tables([JobModel])
        self.database.close()

    def test_enqueue_redacts_nested_credentials_without_mutating_kwargs(self):
        password = "fake-registry-pw"
        kwargs = {
            "registry": {"username": "registry-user", "password": password},
            "nested": [{"API_KEY": "fake-api-key"}],
            "description": "keep this value",
        }

        record = Job()
        record.enqueue("Example", ExampleJob().run, (), kwargs)

        persisted = json.loads(record.model.data)
        self.assertEqual(persisted["kwargs"]["registry"]["password"], "[REDACTED]")
        self.assertEqual(persisted["kwargs"]["nested"][0]["API_KEY"], "[REDACTED]")
        self.assertEqual(persisted["kwargs"]["description"], "keep this value")
        self.assertNotIn(password, record.model.data)
        self.assertEqual(kwargs["registry"]["password"], password)
        self.assertEqual(kwargs["nested"][0]["API_KEY"], "fake-api-key")

    def test_enqueue_passes_original_credentials_to_rq(self):
        registry = {"username": "registry-user", "password": "fake-registry-pw"}
        queued = Mock()

        with patch("agent.job.connection", return_value=Mock()), patch(
            "agent.job.get_current_job", return_value=None
        ), patch("agent.job.get_agent_job_id", return_value=None), patch(
            "agent.job.queue", return_value=queued
        ):
            EnqueueExample().run(registry=registry)

        self.assertIs(queued.enqueue_call.call_args.kwargs["kwargs"]["registry"], registry)

    def test_stop_drops_rq_call_payload_and_description(self):
        password = "fake-registry-pw"
        record = Job()
        record.model = JobModel.create(name="Example", status="Running", start=datetime.now())
        record.redis = Mock()
        record.job = Mock()
        record.job.get_id.return_value = "1"
        record.job.to_dict.return_value = {
            "data": f"compressed payload containing {password}",
            "description": f"ExampleJob.run(registry={{'password': '{password}'}})",
            "meta": {"token": password},
            "status": "started",
        }

        with patch("agent.job.send_stop_job_command"):
            record.stop()

        persisted = json.loads(record.model.data)
        self.assertNotIn("data", persisted)
        self.assertNotIn("description", persisted)
        self.assertEqual(persisted["meta"]["token"], "[REDACTED]")
        self.assertNotIn(password, record.model.data)
