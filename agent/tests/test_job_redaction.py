from __future__ import annotations

import json
import logging
import sys
import unittest
import weakref
from datetime import datetime
from unittest.mock import Mock, patch

from peewee import SqliteDatabase
from rq import Worker
from rq.job import Job as RQJob

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

    def test_real_rq_description_does_not_include_execution_arguments(self):
        password = "synthetic-rq-description-canary"
        created = []

        def enqueue(function, **options):
            options["id"] = options.pop("job_id")
            created.append(RQJob.create(function, connection=Mock(), **options))

        queued = Mock()
        queued.enqueue_call.side_effect = enqueue
        with patch("agent.job.connection", return_value=Mock()), patch(
            "agent.job.get_current_job", return_value=None
        ), patch("agent.job.get_agent_job_id", return_value=None), patch(
            "agent.job.queue", return_value=queued
        ):
            EnqueueExample().run(registry={"password": password})

        rq_job = created[0]
        self.assertTrue(rq_job.kwargs["registry"]["password"] == password)
        self.assertTrue(password not in rq_job.description)
        self.assertTrue(password not in rq_job.to_dict()["description"])
        with self.assertLogs("rq.worker", level=logging.INFO) as captured:
            logging.getLogger("rq.worker").info("default: %s (%s)", rq_job.description, rq_job.id)
        self.assertTrue(password not in str(captured.output))

    def test_real_rq_worker_error_records_omit_argument_payloads(self):
        password = "synthetic-rq-error-canary"
        rq_job = RQJob.create(
            ExampleJob().run,
            args=({"password": password},),
            kwargs={"registry": {"password": password}},
            connection=Mock(),
            description="Example",
        )
        worker = Worker.__new__(Worker)
        worker.log = logging.getLogger("rq.worker")
        worker._exc_handlers = []
        try:
            raise RuntimeError("synthetic failure")
        except RuntimeError:
            with self.assertLogs("rq.worker", level=logging.ERROR) as captured:
                worker.handle_exception(rq_job, *sys.exc_info())
        self.assertTrue(password not in str(captured.records[0].__dict__))
        self.assertEqual(captured.records[0].job_id, rq_job.id)
        self.assertEqual(captured.records[0].func, rq_job.func_name)
        self.assertTrue(rq_job.args[0]["password"] == password)
        self.assertTrue(rq_job.kwargs["registry"]["password"] == password)

    def test_real_sentry_rq_event_drops_arguments_and_frame_locals(self):
        import sentry_sdk
        from sentry_sdk.integrations.rq import _make_event_processor

        password = "synthetic-sentry-canary"
        rq_job = RQJob.create(
            ExampleJob().run,
            args=(password,),
            kwargs={"registry": {"password": password}},
            connection=Mock(),
            description="Example",
        )
        from agent.job import sanitize_sentry_event

        events = []
        with sentry_sdk.init(
            dsn="https://public@example.test/1",
            transport=events.append,
            default_integrations=False,
            include_local_variables=False,
            before_send=sanitize_sentry_event,
        ), sentry_sdk.new_scope() as scope:
            scope.add_event_processor(_make_event_processor(weakref.ref(rq_job)))
            try:
                raise RuntimeError("synthetic failure")
            except RuntimeError:
                sentry_sdk.capture_exception()
        self.assertEqual(len(events), 1)
        self.assertTrue(password not in str(events[0]))
        self.assertEqual(events[0]["extra"]["rq-job"]["job_id"], rq_job.id)
        self.assertEqual(events[0]["extra"]["rq-job"]["func"], rq_job.func_name)
        self.assertTrue(rq_job.kwargs["registry"]["password"] == password)

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
