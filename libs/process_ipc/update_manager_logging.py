#!/usr/bin/env python3
"""
Script to update all log_process_event calls in the process manager to use
native universal_logging.
"""

import re
from pathlib import Path


def update_logging_calls():
    """Update all log_process_event calls to use structured_logger.log_operation."""

    file_path = str(Path(__file__).resolve().parent / "process" / "manager.py")

    # Read the file
    with open(file_path) as f:
        content = f.read()

    # Pattern to match log_process_event calls
    # This matches the function call and captures the arguments
    pattern = r"log_process_event\(\s*self\._logger,\s*([^,]+),\s*([^,]+),\s*([^)]+)\)"

    def replace_call(match):
        process_id = match.group(1).strip()
        event = match.group(2).strip()
        remaining_args = match.group(3).strip()

        # Handle the remaining arguments (level, **kwargs)
        if remaining_args:
            # Extract level if present
            level_match = re.search(r'level\s*=\s*["\']([^"\']+)["\']', remaining_args)
            level_match.group(1) if level_match else "INFO"

            # Extract other kwargs
            kwargs = []
            for arg in remaining_args.split(","):
                arg = arg.strip()
                if arg and not arg.startswith("level="):
                    kwargs.append(arg)

            kwargs_str = ", " + ", ".join(kwargs) if kwargs else ""
        else:
            kwargs_str = ""

        # Determine success based on event name
        success = not any(
            keyword in event.lower()
            for keyword in ["failed", "error", "exception", "timeout", "dead"]
        )

        # Create the replacement
        replacement = (
            "self._structured_logger.log_operation(\n"
            '                    "process",\n'
            f'                    f"{process_id}:{event}",\n'
            f"                    {success}{kwargs_str}\n"
            "                )"
        )

        return replacement

    # Apply the replacement
    updated_content = re.sub(
        pattern, replace_call, content, flags=re.MULTILINE | re.DOTALL
    )

    # Write the updated content back
    with open(file_path, "w") as f:
        f.write(updated_content)

    print(f"Updated log_process_event calls in {file_path}")


if __name__ == "__main__":
    update_logging_calls()
