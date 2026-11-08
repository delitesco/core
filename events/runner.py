from app.log import LOG
from app.db import Session
from app.models import SyncEvent
from events.event_sink import EventSink
from events.event_source import EventSource


class Runner:
    def __init__(self, source: EventSource, sink: EventSink, service_name: str = ""):
        self.__source = source
        self.__sink = sink
        self.__service_name = service_name

    def run(self):
        self.__source.run(self.__on_event)

    def __on_event(self, event: SyncEvent):
        try:
            success = self.__sink.process(event)
            if success:
                event_id = event.id
                SyncEvent.delete(event.id, commit=True)
                LOG.info(f"Marked {event_id} as done")

            else:
                event.retry_count = event.retry_count + 1
                Session.commit()
        except Exception as e:
            LOG.warning(f"Exception processing event [id={event.id}]: {e}")
