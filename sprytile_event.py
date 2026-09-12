"""Minimal synchronous event source used to fan modal events out to the tools.

Sprytile used to bundle RxPY 1.6 for this, but only ever used a hot,
synchronous, multicast source with a filter operator: five methods out of 245
files. Replacing it was housekeeping rather than a porting requirement, RxPY 1.6
imports fine on the Python versions Blender ships. What it does need is the
addon folder on ``sys.path``, because it resolves its own modules with absolute
``from rx...`` imports, so it registers a global ``rx`` that collides with any
other addon bundling its own copy. It also calls ``collections.Iterable``, which
Python 3.10 removed, in a few operators Sprytile never reached.

The behaviour deliberately mirrors what the old chain did::

    Observable.create(fn).publish().auto_connect(1)

* ``fn`` is handed an observer on the *first* subscribe
* ``on_next`` fans out to subscribers in subscription order
* a subscriber raising from ``on_next`` has its subscription dropped and the
  exception keeps travelling up to the caller
* a predicate raising is reported to that subscriber's ``on_error`` and drops
  the subscription, without interrupting the other subscribers
* ``on_completed`` / ``on_error`` notify every subscriber once, after which the
  source is stopped and ignores further events
"""


class _Subscriber:
    __slots__ = ("on_next", "on_error", "on_completed", "predicate")

    def __init__(self, on_next, on_error, on_completed, predicate):
        self.on_next = on_next
        self.on_error = on_error
        self.on_completed = on_completed
        self.predicate = predicate


class Subscription:
    """Handle returned by subscribe, lets a subscriber detach early."""

    def __init__(self, source, subscriber):
        self.source = source
        self.subscriber = subscriber

    def dispose(self):
        if self.subscriber is not None:
            self.source.remove_subscriber(self.subscriber)
            self.subscriber = None


class FilteredEventSource:
    """The result of EventSource.filter, only exists to carry the predicate."""

    def __init__(self, source, predicate):
        self.source = source
        self.predicate = predicate

    def subscribe(self, on_next=None, on_error=None, on_completed=None):
        return self.source.subscribe(on_next, on_error, on_completed, self.predicate)


class EventSource:
    """Both the observable the tools subscribe to and the observer the modal
    operator pushes events into."""

    def __init__(self, on_first_subscribe=None):
        self.on_first_subscribe = on_first_subscribe
        self.subscribers = []
        self.is_connected = False
        self.is_stopped = False

    # ==============
    # Observable side
    # ==============
    def filter(self, predicate):
        return FilteredEventSource(self, predicate)

    def subscribe(self, on_next=None, on_error=None, on_completed=None, predicate=None):
        subscriber = _Subscriber(on_next, on_error, on_completed, predicate)
        self.subscribers.append(subscriber)

        if not self.is_connected:
            self.is_connected = True
            if self.on_first_subscribe is not None:
                self.on_first_subscribe(self)

        return Subscription(self, subscriber)

    def remove_subscriber(self, subscriber):
        if subscriber in self.subscribers:
            self.subscribers.remove(subscriber)

    # ============
    # Observer side
    # ============
    def on_next(self, value):
        if self.is_stopped:
            return

        # Copy, subscribers can be dropped while dispatching
        for subscriber in tuple(self.subscribers):
            if subscriber.predicate is not None:
                try:
                    if not subscriber.predicate(value):
                        continue
                except Exception as err:
                    self.remove_subscriber(subscriber)
                    if subscriber.on_error is not None:
                        subscriber.on_error(err)
                    continue

            if subscriber.on_next is None:
                continue
            try:
                subscriber.on_next(value)
            except Exception:
                self.remove_subscriber(subscriber)
                raise

    def on_error(self, error):
        if self.is_stopped:
            return
        self.is_stopped = True

        subscribers, self.subscribers = self.subscribers, []
        for subscriber in subscribers:
            if subscriber.on_error is not None:
                subscriber.on_error(error)

    def on_completed(self):
        if self.is_stopped:
            return
        self.is_stopped = True

        subscribers, self.subscribers = self.subscribers, []
        for subscriber in subscribers:
            if subscriber.on_completed is not None:
                subscriber.on_completed()
