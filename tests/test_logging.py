import logging
from custom_components.scheduler.const import SchedulerLogger

class MockLogger:
    def __init__(self):
        self.messages = []
    def debug(self, msg, *args, **kwargs):
        self.messages.append(("DEBUG", msg))
    def info(self, msg, *args, **kwargs):
        self.messages.append(("INFO", msg))

def test_scheduler_logger_formatting():
    mock = MockLogger()
    logger = SchedulerLogger(mock, "test_id")

    logger.info("Hello World")

    assert len(mock.messages) == 1
    assert mock.messages[0] == ("INFO", "[test_id]: Hello World")

def test_scheduler_logger_no_id():
    mock = MockLogger()
    logger = SchedulerLogger(mock)

    logger.debug("Hello World")

    assert len(mock.messages) == 1
    assert mock.messages[0] == ("DEBUG", "Hello World")
