/**
 * Mutation → argv. Pure, no Raycast and no child_process, so it can be
 * asserted directly — which matters, because a wrong flag here surfaces as
 * clap exiting 2 with "unexpected argument", i.e. as a mystery.
 *
 * Every flag emitted here is checked against `pueue <subcommand> --help` by
 * `just verify-argv`.
 */

import type { Mutation } from "./transport";

export function argvFor(m: Mutation): string[] {
  switch (m.op) {
    case "add": {
      const a = ["add"];
      if (m.workingDirectory) a.push("--working-directory", m.workingDirectory);
      if (m.group) a.push("--group", m.group);
      if (m.label) a.push("--label", m.label);
      if (m.priority !== undefined) a.push("--priority", String(m.priority));
      for (const id of m.after ?? []) a.push("--after", String(id));
      if (m.delay) a.push("--delay", m.delay);
      if (m.stashed) a.push("--stashed");
      if (m.immediate) a.push("--immediate");
      if (m.escape) a.push("--escape");
      a.push("--print-task-id");
      // The command goes last, after `--`, as ONE argv element. pueue joins its
      // variadic <COMMAND>... with spaces and hands the result to `sh -c`, so
      // quoting it here would double-escape it. Never build a shell string.
      a.push("--", m.command);
      return a;
    }
    case "start": {
      const a = ["start"];
      if (m.all) a.push("--all");
      if (m.group) a.push("--group", m.group);
      a.push(...(m.ids ?? []).map(String));
      return a;
    }
    case "pause": {
      const a = ["pause"];
      if (m.all) a.push("--all");
      if (m.group) a.push("--group", m.group);
      if (m.wait) a.push("--wait");
      a.push(...(m.ids ?? []).map(String));
      return a;
    }
    case "kill": {
      const a = ["kill"];
      if (m.all) a.push("--all");
      if (m.group) a.push("--group", m.group);
      if (m.signal) a.push("--signal", m.signal);
      a.push(...(m.ids ?? []).map(String));
      return a;
    }
    case "restart": {
      const a = ["restart"];
      // Both --in-place and --not-in-place exist because the default comes from
      // the user's config. Being explicit means the action does what it says
      // regardless of whose machine it runs on.
      a.push(m.inPlace ? "--in-place" : "--not-in-place");
      if (m.stashed) a.push("--stashed");
      if (m.immediate) a.push("--immediate");
      a.push(...m.ids.map(String));
      return a;
    }
    case "remove":
      return ["remove", ...m.ids.map(String)];
    case "stash": {
      const a = ["stash"];
      if (m.group) a.push("--group", m.group);
      if (m.delay) a.push("--delay", m.delay);
      a.push(...(m.ids ?? []).map(String));
      return a;
    }
    case "enqueue": {
      const a = ["enqueue"];
      if (m.group) a.push("--group", m.group);
      if (m.delay) a.push("--delay", m.delay);
      a.push(...(m.ids ?? []).map(String));
      return a;
    }
    case "clean": {
      const a = ["clean"];
      if (m.successfulOnly) a.push("--successful-only");
      if (m.group) a.push("--group", m.group);
      return a;
    }
    case "switch":
      return ["switch", String(m.a), String(m.b)];
    case "parallel": {
      const a = ["parallel", String(m.count)];
      if (m.group) a.push("--group", m.group);
      return a;
    }
    case "group-add": {
      const a = ["group", "add", m.name];
      if (m.parallel !== undefined) a.push("--parallel", String(m.parallel));
      return a;
    }
    case "group-remove":
      return ["group", "remove", m.name];
    case "send":
      return ["send", String(m.id), m.input];
    case "reset": {
      const a = ["reset", "--force"];
      for (const g of m.groups ?? []) a.push("--groups", g);
      return a;
    }
  }
}

/** The subcommand path an argv addresses, e.g. ["group","add"]. For flag checking. */
export function subcommandOf(argv: string[]): string[] {
  return argv[0] === "group" ? argv.slice(0, 2) : argv.slice(0, 1);
}

/** Long flags an argv uses, in order. For checking against `--help`. */
export function longFlagsOf(argv: string[]): string[] {
  const out: string[] = [];
  for (const a of argv) {
    if (a === "--") break;
    if (a.startsWith("--")) out.push(a);
  }
  return out;
}
