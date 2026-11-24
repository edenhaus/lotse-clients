"""The base class of every generated model.

Encoding omits fields that are `None`: the daemon rejects a `null` where the
Rust type has a default but no `Option` (a source's `options`, for one), and
leaving a field out always means its default. Decoding ignores fields it does
not know, because a newer daemon may add them without an API version bump.
"""

from mashumaro.config import BaseConfig
from mashumaro.mixins.orjson import DataClassORJSONMixin


class Model(DataClassORJSONMixin):
    """A message type of the control API, encoded and decoded by mashumaro."""

    class Config(BaseConfig):
        """Leave out `None` when encoding."""

        omit_none = True
