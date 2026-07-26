/**
 * Reading a task's log off disk.
 *
 * Separate from `cli-transport.ts` so it stays free of Raycast imports and the
 * tail logic can be asserted directly — getting the partial-line trim wrong
 * shows up as a corrupted first line, which is easy to miss by eye.
 *
 * pueue writes stdout and stderr interleaved into one uncompressed plain-text
 * file per task. Reading it directly is what `pueue log` itself does when
 * `client.read_local_logs` is true, which is the default.
 */

import { open, readFile, stat } from "node:fs/promises";

export interface LogTail {
  text: string;
  /** True when the beginning was cut off. */
  truncated: boolean;
  path: string;
}

/**
 * The last `maxBytes` of a file.
 *
 * `pueue log --full`'s own help warns it can use all of your machine's RAM, and
 * a build log really can be hundreds of megabytes. Reading a window from the
 * end avoids that entirely, at the cost of dropping the partial line the window
 * usually starts in the middle of.
 *
 * Returns undefined when the file isn't there — a remote daemon,
 * `read_local_logs: false`, or a `pueue_directory` we couldn't resolve — so
 * callers can fall back to the CLI.
 */
export async function readLogTail(
  path: string,
  maxBytes = 512 * 1024,
): Promise<LogTail | undefined> {
  try {
    const { size } = await stat(path);

    if (size <= maxBytes) {
      return { text: await readFile(path, "utf8"), truncated: false, path };
    }

    const handle = await open(path, "r");
    try {
      const buf = Buffer.alloc(maxBytes);
      await handle.read(buf, 0, maxBytes, size - maxBytes);
      // The window almost certainly opens mid-line. Dropping through the first
      // newline costs one line and avoids showing a fragment as if it were
      // real output. Also guards against a multi-byte character split in half.
      const raw = buf.toString("utf8");
      const nl = raw.indexOf("\n");
      return {
        text: nl === -1 ? raw : raw.slice(nl + 1),
        truncated: true,
        path,
      };
    } finally {
      await handle.close();
    }
  } catch {
    return undefined;
  }
}
