import logging
import os
import shutil
import traceback
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Optional

from pysqlcipher3 import dbapi2 as sqlcipher

from rotkehlchen.api.websockets.typedefs import WSMessageType
from rotkehlchen.db.settings import ROTKEHLCHEN_DB_VERSION
from rotkehlchen.db.upgrades.v26_v27 import upgrade_v26_to_v27
from rotkehlchen.db.upgrades.v27_v28 import upgrade_v27_to_v28
from rotkehlchen.db.upgrades.v28_v29 import upgrade_v28_to_v29
from rotkehlchen.db.upgrades.v29_v30 import upgrade_v29_to_v30
from rotkehlchen.db.upgrades.v30_v31 import upgrade_v30_to_v31
from rotkehlchen.db.upgrades.v31_v32 import upgrade_v31_to_v32
from rotkehlchen.db.upgrades.v32_v33 import upgrade_v32_to_v33
from rotkehlchen.db.upgrades.v33_v34 import upgrade_v33_to_v34
from rotkehlchen.db.upgrades.v34_v35 import upgrade_v34_to_v35
from rotkehlchen.db.upgrades.v35_v36 import upgrade_v35_to_v36
from rotkehlchen.db.upgrades.v36_v37 import upgrade_v36_to_v37
from rotkehlchen.db.upgrades.v37_v38 import upgrade_v37_to_v38
from rotkehlchen.db.upgrades.v38_v39 import upgrade_v38_to_v39
from rotkehlchen.db.upgrades.v39_v40 import upgrade_v39_to_v40
from rotkehlchen.errors.misc import DBUpgradeError
from rotkehlchen.logging import RotkehlchenLogsAdapter
from rotkehlchen.utils.interfaces import ProgressUpdater
from rotkehlchen.utils.misc import ts_now
from rotkehlchen.utils.upgrades import UpgradeRecord

if TYPE_CHECKING:
    from rotkehlchen.db.dbhandler import DBHandler

MIN_SUPPORTED_USER_DB_VERSION = 26

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


UPGRADES_LIST = [
    UpgradeRecord(
        from_version=26,
        function=upgrade_v26_to_v27,
    ),
    UpgradeRecord(
        from_version=27,
        function=upgrade_v27_to_v28,
    ),
    UpgradeRecord(
        from_version=28,
        function=upgrade_v28_to_v29,
    ),
    UpgradeRecord(
        from_version=29,
        function=upgrade_v29_to_v30,
    ),
    UpgradeRecord(
        from_version=30,
        function=upgrade_v30_to_v31,
    ),
    UpgradeRecord(
        from_version=31,
        function=upgrade_v31_to_v32,
    ),
    UpgradeRecord(
        from_version=32,
        function=upgrade_v32_to_v33,
    ),
    UpgradeRecord(
        from_version=33,
        function=upgrade_v33_to_v34,
    ),
    UpgradeRecord(
        from_version=34,
        function=upgrade_v34_to_v35,
    ),
    UpgradeRecord(
        from_version=35,
        function=upgrade_v35_to_v36,
    ),
    UpgradeRecord(
        from_version=36,
        function=upgrade_v36_to_v37,
    ),
    UpgradeRecord(
        from_version=37,
        function=upgrade_v37_to_v38,
    ),
    UpgradeRecord(
        from_version=38,
        function=upgrade_v38_to_v39,
    ),
    UpgradeRecord(
        from_version=39,
        function=upgrade_v39_to_v40,
    ),
]


class DBUpgradeProgressHandler(ProgressUpdater):
    """Class to notify users through websockets about progress of upgrading the database."""

    def _notify_frontend(self, step_name: Optional[str] = None) -> None:
        """Sends to the user through websockets all information about db upgrading progress."""
        self.messages_aggregator.add_message(
            message_type=WSMessageType.DB_UPGRADE_STATUS,
            data={
                'start_version': self.start_version,
                'target_version': self.target_version,
                'current_upgrade': {
                    'to_version': self.current_version,
                    'total_steps': self.current_round_total_steps,
                    'current_step': self.current_round_current_step,
                },
            },
        )


class DBUpgradeManager:
    """Separate class to manage DB upgrades/migrations"""

    def __init__(self, db: 'DBHandler'):
        self.db = db

    def run_upgrades(self) -> bool:
        """Run all required database upgrades

        Returns true for fresh database and false otherwise.

        May raise:
        - DBUpgradeError if the user uses a newer version than the one we
        upgrade to or if there is a problem during upgrade or if the version is older
        than the one supported.
        """
        with self.db.conn.write_ctx() as cursor:
            try:
                our_version = self.db.get_setting(cursor, 'version')
            except sqlcipher.OperationalError:  # pylint: disable=no-member
                return True  # fresh database. Nothing to upgrade.

            if our_version < MIN_SUPPORTED_USER_DB_VERSION:
                raise DBUpgradeError(
                    f'Your account was last opened by a very old version of rotki and its '
                    f'version is {our_version}. To be able to use it you will need to '
                    f'first use a previous version of rotki and then use this one. '
                    f'Refer to the documentation for more information. '
                    f'https://rotki.readthedocs.io/en/latest/usage_guide.html#upgrading-rotki-after-a-very-long-time',
                )

            if our_version > ROTKEHLCHEN_DB_VERSION:
                raise DBUpgradeError(
                    'Your database version is newer than the version expected by the '
                    'executable. Did you perhaps try to revert to an older rotki version? '
                    'Please only use the latest version of the software.',
                )

        progress_handler = DBUpgradeProgressHandler(
            messages_aggregator=self.db.msg_aggregator,
            target_version=ROTKEHLCHEN_DB_VERSION,
        )
        for upgrade in UPGRADES_LIST:
            self._perform_single_upgrade(upgrade, progress_handler)

        # Finally make sure to always have latest version in the DB
        with self.db.user_write() as cursor:
            self.db.set_setting(cursor, name='version', value=ROTKEHLCHEN_DB_VERSION)
        return False

    def _perform_single_upgrade(
            self,
            upgrade: UpgradeRecord,
            progress_handler: DBUpgradeProgressHandler,
    ) -> None:
        """
        This is the wrapper function that performs each DB upgrade

        The logic is:
            1. Check version, if not at from_version get out.
            2. If at from_version make a DB backup before performing the upgrade
            3. Perform the upgrade action
            4. If something went wrong during upgrade restore backup and quit
            5. If all went well set version and delete the backup

        """
        with self.db.conn.read_ctx() as cursor:
            current_version = self.db.get_setting(cursor, 'version')
        if current_version != upgrade.from_version:
            return
        to_version = upgrade.from_version + 1
        progress_handler.new_round(version=to_version)

        # First make a backup of the DB
        with TemporaryDirectory() as tmpdirname:
            tmp_db_filename = f'{ts_now()}_rotkehlchen_db_v{upgrade.from_version}.backup'
            tmp_db_path = os.path.join(tmpdirname, tmp_db_filename)
            shutil.copyfile(
                os.path.join(self.db.user_data_dir, 'rotkehlchen.db'),
                tmp_db_path,
            )

            # Add a flag to the db that an upgrade is happening
            with self.db.user_write() as write_cursor:
                self.db.set_setting(
                    write_cursor=write_cursor,
                    name='ongoing_upgrade_from_version',
                    value=upgrade.from_version,
                )

            try:
                kwargs = upgrade.kwargs if upgrade.kwargs is not None else {}
                upgrade.function(db=self.db, progress_handler=progress_handler, **kwargs)
            except BaseException as e:
                # Problem .. restore DB backup, log all info and bail out
                error_message = (
                    f'Failed at database upgrade from version {upgrade.from_version} to '
                    f'{to_version}: {e!s}'
                )
                stacktrace = traceback.format_exc()
                log.error(f'{error_message}\n{stacktrace}')
                shutil.copyfile(
                    tmp_db_path,
                    os.path.join(self.db.user_data_dir, 'rotkehlchen.db'),
                )
                raise DBUpgradeError(error_message) from e

            # even for success keep the backup of the previous db
            shutil.copyfile(
                tmp_db_path,
                os.path.join(self.db.user_data_dir, tmp_db_filename),
            )

        # Upgrade success all is good
        with self.db.user_write() as cursor:
            cursor.execute('DELETE FROM settings WHERE name=?', ('ongoing_upgrade_from_version',))
            self.db.set_setting(write_cursor=cursor, name='version', value=to_version)
