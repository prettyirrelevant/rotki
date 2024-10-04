import logging
from typing import TYPE_CHECKING

from rotkehlchen.chain.evm.decoding.thegraph.constants import CPT_THEGRAPH
from rotkehlchen.db.filtering import EvmEventFilterQuery
from rotkehlchen.db.history_events import DBHistoryEvents
from rotkehlchen.history.events.structures.types import HistoryEventSubType, HistoryEventType
from rotkehlchen.logging import RotkehlchenLogsAdapter, enter_exit_debug_log
from rotkehlchen.types import Location
from rotkehlchen.utils.misc import address_to_bytes32

if TYPE_CHECKING:
    from rotkehlchen.data_migrations.progress import MigrationProgressHandler
    from rotkehlchen.rotkehlchen import Rotkehlchen

logger = logging.getLogger(__name__)
log = RotkehlchenLogsAdapter(logger)


@enter_exit_debug_log()
def cleanup_extra_thegraph_txs(rotki: 'Rotkehlchen') -> None:
    dbevents = DBHistoryEvents(rotki.data.db)
    with rotki.data.db.conn.read_ctx() as cursor:
        tracked_addresses = rotki.data.db.get_evm_accounts(cursor)

    if len(tracked_addresses) == 0:
        return

    db_filter = EvmEventFilterQuery.make(
        counterparties=[CPT_THEGRAPH],
        location=Location.ETHEREUM,
        event_types=[HistoryEventType.INFORMATIONAL],
        event_subtypes=[HistoryEventSubType.APPROVE],
        location_labels=tracked_addresses,  # type: ignore  # sequence[address] == list[str]
        order_by_rules=[('timestamp', True)],  # order by timestamp in ascending order
    )
    with rotki.data.db.conn.read_ctx() as cursor:
        events = dbevents.get_history_events(
            cursor=cursor,
            filter_query=db_filter,
            has_premium=True,
        )
    approved_delegators = {x.address for x in events if x.address is not None}
    all_addresses = approved_delegators | set(tracked_addresses)

    tracked_placeholders = ','.join('?' * len(tracked_addresses))
    delegators_placeholders = ','.join('?' * len(approved_delegators))
    all_placeholders = ','.join('?' * (len(approved_delegators) + len(tracked_addresses)))
    # Find all possible logs we need to keep for our related addresses and mark the transactions
    querystr = f"""SELECT DISTINCT et.tx_hash FROM evm_transactions et
JOIN evmtx_receipts er ON et.identifier = er.tx_id
JOIN evmtx_receipt_logs erl ON er.tx_id = erl.tx_id
JOIN evmtx_receipt_log_topics erlt0 ON erl.identifier = erlt0.log
JOIN evmtx_receipt_log_topics erlt2 ON erl.identifier = erlt2.log
WHERE erl.address = '0xF55041E37E12cD407ad00CE2910B8269B01263b9'
  AND
    (erlt0.topic_index = 0  /* DelegationTransferredToL2 */
  AND erlt0.topic = X'231E5CFEFF7759A468241D939AB04A60D603B17E359057ABBB8F52AFC3E4986B'
AND ((
     erlt2.topic_index = 2
     AND erlt2.topic IN ({tracked_placeholders}))"""
    query_bindings = [address_to_bytes32(x) for x in tracked_addresses]

    if len(approved_delegators) != 0:
        querystr += f' OR (erlt2.topic_index = 1 AND erlt2.topic IN({delegators_placeholders}))'
        query_bindings += [address_to_bytes32(x) for x in approved_delegators]

    querystr += f"""
    ))OR
    ((erlt0.topic_index = 0  /* StakeDelegationWithdrawn */
    AND erlt0.topic = X'1B2E7737E043C5CF1B587CEB4DAEB7AE00148B9BDA8F79F1093EEAD08F141952') AND (erlt2.topic_index = 2 AND erlt2.topic IN ({all_placeholders})))
    """  # noqa: E501
    query_bindings += [address_to_bytes32(x) for x in all_addresses]

    querystr += f"""
    OR
    ((erlt0.topic_index = 0  /* StakeDelegated */
    AND erlt0.topic = X'CD0366DCE5247D874FFC60A762AA7ABBB82C1695BBB171609C1B8861E279EB73') AND (erlt2.topic_index = 2 AND erlt2.topic IN ({all_placeholders})))
    """  # noqa: E501
    query_bindings += [address_to_bytes32(x) for x in all_addresses]

    querystr += f"""
    OR
    ((erlt0.topic_index = 0  /* StakeDelegatedLocked */
    AND erlt0.topic = X'0430183F84D9C4502386D499DA806543DEE1D9DE83C08B01E39A6D2116C43B25') AND (erlt2.topic_index = 2 AND erlt2.topic IN ({all_placeholders})))
    """  # noqa: E501
    query_bindings += [address_to_bytes32(x) for x in all_addresses]

    to_keep_hashes = []
    with rotki.data.db.conn.read_ctx() as cursor:
        cursor.execute(querystr, query_bindings)
        to_keep_hashes = [x[0] for x in cursor]

    # Finally delete the unneeded transactions
    query_bindings = []
    querystr = """DELETE FROM evm_transactions
    WHERE identifier IN (
    SELECT DISTINCT et.identifier
    FROM evm_transactions et
    JOIN evmtx_receipts er ON et.identifier = er.tx_id
    JOIN evmtx_receipt_logs erl ON er.tx_id = erl.tx_id
    WHERE erl.address = '0xF55041E37E12cD407ad00CE2910B8269B01263b9'
) AND tx_hash NOT IN (SELECT tx_hash from evm_events_info)"""
    if len(to_keep_hashes) != 0:
        # we have also performed a thorough logs query above in case some were not decoded.
        # As if the transactions were not decoded yet then the
        # tx_hash NOT IN (SELECT tx_hash from evm_events_info) won't help avoid
        # deleting important transactions
        to_keep_placeholders = ','.join('?' * len(to_keep_hashes))
        querystr += f' AND tx_hash NOT IN ({to_keep_placeholders})'
        query_bindings += to_keep_hashes

    with rotki.data.db.conn.write_ctx() as write_cursor:
        write_cursor.execute(querystr, query_bindings)

    rotki.data.db.conn.execute('VACUUM;')  # also since this cleans up a lot of space vacuum


def data_migration_18(rotki: 'Rotkehlchen', progress_handler: 'MigrationProgressHandler') -> None:  # pylint: disable=unused-argument
    """
    Introduced at v1.35.1

    Fix the issue of extra transactions saved in the DB from the graph queries
    for delegation to arbitrum
    """
    progress_handler.set_total_steps(1)
    cleanup_extra_thegraph_txs(rotki)
    progress_handler.new_step()