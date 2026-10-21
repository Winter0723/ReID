import datetime


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def as_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value.astimezone(datetime.UTC)
