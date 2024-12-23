import { TaskType } from '@/types/task-type';
import { logger } from '@/utils/logging';
import { useNotificationsStore } from '@/store/notifications';
import { useTaskStore } from '@/store/tasks';
import { useHistoryApi } from '@/composables/api/history';
import type { EvmUnDecodedTransactionsData, ProtocolCacheUpdatesData } from '@/types/websocket-messages';

export const useHistoryStore = defineStore('history', () => {
  const associatedLocations = ref<string[]>([]);
  const undecodedTransactionsStatus = ref<Record<string, EvmUnDecodedTransactionsData>>({});
  const protocolCacheUpdateStatus = ref<Record<string, ProtocolCacheUpdatesData>>({});

  const receivingProtocolCacheStatus = ref<boolean>(false);

  const decodingStatus = computed<EvmUnDecodedTransactionsData[]>(() =>
    Object.values(get(undecodedTransactionsStatus)).filter(status => status.total > 0),
  );

  const protocolCacheStatus = computed<ProtocolCacheUpdatesData[]>(() =>
    Object.values(get(protocolCacheUpdateStatus)).filter(status => status.total > 0),
  );

  const setUndecodedTransactionsStatus = (data: EvmUnDecodedTransactionsData): void => {
    set(receivingProtocolCacheStatus, false);
    set(undecodedTransactionsStatus, {
      ...get(undecodedTransactionsStatus),
      [data.chain]: data,
    });
  };

  const updateUndecodedTransactionsStatus = (data: Record<string, EvmUnDecodedTransactionsData>): void => {
    set(undecodedTransactionsStatus, {
      ...get(undecodedTransactionsStatus),
      ...data,
    });
  };

  const { isTaskRunning } = useTaskStore();
  const refreshProtocolCacheTaskRunning = isTaskRunning(TaskType.REFRESH_GENERAL_CACHE);

  const setProtocolCacheStatus = (data: ProtocolCacheUpdatesData): void => {
    set(receivingProtocolCacheStatus, true);
    const old = get(protocolCacheUpdateStatus);
    const filtered: Record<string, ProtocolCacheUpdatesData> = {};
    const currentKey = `${data.chain}#${data.protocol}`;
    for (const key in old) {
      if (key !== currentKey) {
        filtered[key] = {
          ...old[key],
          processed: old[key].total,
        };
      }
    }
    set(protocolCacheUpdateStatus, {
      [currentKey]: data,
      ...filtered,
    });
  };

  const resetUndecodedTransactionsStatus = (): void => {
    set(undecodedTransactionsStatus, {});
  };

  const resetProtocolCacheUpdatesStatus = (): void => {
    set(protocolCacheUpdateStatus, {});
  };

  const { fetchAssociatedLocations: fetchAssociatedLocationsApi } = useHistoryApi();
  const { notify } = useNotificationsStore();
  const { t } = useI18n();

  const getUndecodedTransactionStatus = (): EvmUnDecodedTransactionsData[] =>
    Object.values(get(undecodedTransactionsStatus));

  const fetchAssociatedLocations = async (): Promise<void> => {
    try {
      set(associatedLocations, await fetchAssociatedLocationsApi());
    }
    catch (error: any) {
      logger.error(error);
      const message = error?.message ?? error ?? '';
      notify({
        display: true,
        message: t('actions.history.fetch_associated_locations.error.message', { message }),
        title: t('actions.history.fetch_associated_locations.error.title'),
      });
    }
  };

  watch(refreshProtocolCacheTaskRunning, (curr, prev) => {
    if (!curr && prev) {
      set(receivingProtocolCacheStatus, false);
      resetProtocolCacheUpdatesStatus();
    }
  });

  return {
    associatedLocations,
    decodingStatus,
    fetchAssociatedLocations,
    getUndecodedTransactionStatus,
    protocolCacheStatus,
    receivingProtocolCacheStatus,
    resetProtocolCacheUpdatesStatus,
    resetUndecodedTransactionsStatus,
    setProtocolCacheStatus,
    setUndecodedTransactionsStatus,
    undecodedTransactionsStatus,
    updateUndecodedTransactionsStatus,
  };
});
