"""Not a pytest plugin.

The hermetic guard is installed by ``scripts/model_manager/ui/conftest.py``
for the controller tree and the charter-harvest module only. Loading this
package as ``pytest_plugins`` would make any autouse fixture session-global.
"""
