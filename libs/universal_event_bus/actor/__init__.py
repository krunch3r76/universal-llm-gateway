"""
Actor module for lock-free sequential execution.

Provides patterns that eliminate the need for locks by ensuring
operations are processed one at a time.

Patterns (from simplest to most structured):

1. SequentialExecutor - Composable executor for any class
   executor = SequentialExecutor()
   result = await executor.run(some_coroutine())

2. @sequential decorator - Marks methods as sequential
   class MyClass(Sequential):
       @sequential
       async def my_method(self): ...

3. CommandProcessor - Full command/result pattern (when needed)
   class MyProcessor(CommandProcessor[State]):
       async def _process_command(self, cmd): ...

Prefer simpler patterns. Use CommandProcessor only when you need
explicit command routing or state snapshots.
"""

from .command_processor import Command, CommandProcessor, CommandResult
from .sequential import Sequential, SequentialExecutor, sequential

__all__ = [
    # Simple patterns (preferred)
    "SequentialExecutor",
    "Sequential",
    "sequential",
    # Full command pattern (when needed)
    "Command",
    "CommandProcessor",
    "CommandResult",
]
