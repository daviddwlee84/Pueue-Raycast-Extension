/**
 * Choosing which daemon a view is talking to.
 *
 * Deliberately *not* a `searchBarAccessory`: Raycast allows only one, the group
 * filter already uses it, and most people have no remote connections at all.
 * The switcher lives in the Action Panel and disappears entirely when there is
 * nothing to switch between, so a local-only setup sees no new UI.
 */

import { Action, ActionPanel, Color, Icon, List } from "@raycast/api";
import { useCachedState } from "@raycast/utils";

import {
  connectionByName,
  connections,
  LOCAL_CONNECTION_NAME,
  type Connection,
} from "./pueue";

export interface ConnectionState {
  connection: Connection;
  all: Connection[];
  setName: (name: string) => void;
  /** True when there is more than one to choose from. */
  switchable: boolean;
}

/**
 * The selected connection, remembered across launches.
 *
 * Resolved by name rather than stored whole: the preference can change between
 * launches, and a remembered connection that no longer exists must fall back to
 * local rather than silently keep querying a config that has been deleted.
 */
export function useConnection(key = "connection.name"): ConnectionState {
  const all = connections();
  const [name, setName] = useCachedState(key, LOCAL_CONNECTION_NAME);
  return {
    connection: connectionByName(name),
    all,
    setName,
    switchable: all.length > 1,
  };
}

export function connectionIcon(c: Connection) {
  return c.remote
    ? { source: Icon.Globe, tintColor: Color.Blue }
    : { source: Icon.Desktop, tintColor: Color.SecondaryText };
}

/** An Action Panel submenu for switching. Renders nothing when there's one option. */
export function ConnectionSubmenu({ state }: { state: ConnectionState }) {
  if (!state.switchable) return null;
  return (
    <ActionPanel.Submenu
      title="Connection"
      icon={connectionIcon(state.connection)}
      shortcut={{ modifiers: ["cmd", "shift"], key: "n" }}
    >
      {state.all.map((c) => (
        <Action
          key={c.name}
          title={c.sshHost ? `${c.name} (${c.sshHost})` : c.name}
          icon={
            c.name === state.connection.name
              ? Icon.Checkmark
              : connectionIcon(c)
          }
          onAction={() => state.setName(c.name)}
        />
      ))}
    </ActionPanel.Submenu>
  );
}

/**
 * A row naming the remote daemon a list is showing.
 *
 * Only for remote connections. Which queue you are looking at stops being
 * obvious the moment it isn't this machine's, and acting on the wrong box is
 * the expensive mistake here — task ids are global to a daemon, so a `clean`
 * aimed at the wrong queue takes someone else's history with it.
 */
export function ConnectionBannerItem({ state }: { state: ConnectionState }) {
  const c = state.connection;
  if (!c.remote) return null;
  return (
    <List.Item
      icon={connectionIcon(c)}
      title={c.name}
      subtitle={c.sshHost ? `submits via ssh ${c.sshHost}` : "remote daemon"}
      accessories={[{ tag: { value: "remote", color: Color.Blue } }]}
      actions={
        <ActionPanel>
          <ConnectionSubmenu state={state} />
        </ActionPanel>
      }
    />
  );
}
