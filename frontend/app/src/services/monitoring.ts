import { taskManager } from '@/services/task-manager';
import { QUERY_PERIOD, REFRESH_PERIOD } from '@/store/settings/consts';
import store from '@/store/store';

const PERIODIC = 'periodic';
const TASK = 'task';
const WATCHER = 'watcher';
const BALANCES = 'balances';

class Monitoring {
  private monitors: { [monitor: string]: any } = {};

  private static fetch() {
    store.dispatch('notifications/consume');
    store.dispatch('session/periodicCheck');
  }

  private static fetchWatchers() {
    store.dispatch('session/fetchWatchers');
  }

  private static async fetchBalances() {
    const dispatch = store.dispatch;
    await dispatch('balances/fetchManualBalances');
    await dispatch('balances/fetchBlockchainBalances', { ignoreCache: true });
    await dispatch('balances/fetchLoopringBalances', true);
    await dispatch('balances/fetchConnectedExchangeBalances');
    await dispatch('balances/refreshPrices', true);
  }

  /**
   * This function is called periodically, queries some data from the
   * client and updates the UI with the response.
   */
  start() {
    const settings = store.state.settings!;

    if (!this.monitors[PERIODIC]) {
      Monitoring.fetch();
      this.monitors[PERIODIC] = setInterval(
        Monitoring.fetch,
        settings[QUERY_PERIOD] * 1000
      );
    }

    if (!this.monitors[TASK]) {
      taskManager.monitor();
      this.monitors[TASK] = setInterval(() => taskManager.monitor(), 2000);
    }

    if (!this.monitors[WATCHER]) {
      Monitoring.fetchWatchers();
      // check for watchers every 6 minutes (approx. half the firing time
      // of the server-side watchers)
      this.monitors[WATCHER] = setInterval(Monitoring.fetchWatchers, 360000);
    }

    const period = settings[REFRESH_PERIOD] * 60 * 1000;
    if (!this.monitors[BALANCES] && period > 0) {
      this.monitors[BALANCES] = setInterval(Monitoring.fetchBalances, period);
    }
  }

  stop() {
    for (const key in this.monitors) {
      clearInterval(this.monitors[key]);
      delete this.monitors[key];
    }
  }
}

export const monitor = new Monitoring();
