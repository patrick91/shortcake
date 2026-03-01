import { PatchDiff, type PatchDiffProps } from '@pierre/diffs/react';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';

type DiffStyle = 'unified' | 'split';

type StackBranch = {
  name: string;
  parent: string;
  depth: number;
  isCurrent: boolean;
  commitCount: number;
};

type StackResponse = {
  currentBranch: string | null;
  branches: StackBranch[];
};

type DiffResponse = {
  branch: string;
  parent: string;
  patch: string;
};

type WorkingDiffResponse = {
  patch: string;
};

type DiffSelection =
  | { type: 'branch'; name: string }
  | { type: 'working' };

type FileInfo = {
  path: string;
  name: string;
  additions: number;
  deletions: number;
  patchIndex: number;
};

type DirEntry = {
  type: 'dir';
  name: string;
  path: string;
  children: TreeEntry[];
};

type FileEntry = {
  type: 'file';
  name: string;
  info: FileInfo;
};

type TreeEntry = DirEntry | FileEntry;

const API_BASE = import.meta.env.VITE_SHORTCAKE_API_URL ?? '';
const FILE_TREE_INDENT_BASE = 8;
const FILE_TREE_INDENT_STEP = 10;
const STACK_CARD_INDENT_BASE = 4;
const STACK_CARD_INDENT_STEP = 10;
const STACK_GUIDE_OFFSET = 6;
const STACK_GUIDE_STEP = 10;

function splitPatchIntoFiles(patch: string): string[] {
  if (patch.trim() === '') {
    return [];
  }

  const sections = patch.split(/^diff --git /m);
  if (sections.length <= 1) {
    return [patch];
  }

  return sections
    .slice(1)
    .map((section) => `diff --git ${section}`.trim())
    .filter((section) => section !== '');
}

async function fetchJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  const payload: unknown = await response.json();

  if (!response.ok) {
    const message =
      typeof payload === 'object' &&
      payload !== null &&
      'error' in payload &&
      typeof payload.error === 'string'
        ? payload.error
        : `Request failed (${response.status})`;
    throw new Error(message);
  }

  return payload as T;
}

function parseFileInfo(patch: string, index: number): FileInfo {
  const headerMatch = patch.match(/^diff --git a\/.+ b\/(.+)$/m);
  const path = headerMatch?.[1] ?? `file-${index}`;
  const name = path.split('/').pop() ?? path;

  let additions = 0;
  let deletions = 0;
  let inHunk = false;

  for (const line of patch.split('\n')) {
    if (line.startsWith('@@')) {
      inHunk = true;
      continue;
    }
    if (!inHunk) continue;
    if (line.startsWith('+') && !line.startsWith('+++')) additions++;
    else if (line.startsWith('-') && !line.startsWith('---')) deletions++;
  }

  return { path, name, additions, deletions, patchIndex: index };
}

function buildFileTree(files: FileInfo[]): TreeEntry[] {
  const root: TreeEntry[] = [];

  for (const file of files) {
    const parts = file.path.split('/');
    let current = root;

    for (let i = 0; i < parts.length - 1; i++) {
      const dirName = parts[i]!;
      const dirPath = parts.slice(0, i + 1).join('/');

      let dir = current.find(
        (e): e is DirEntry => e.type === 'dir' && e.name === dirName,
      );

      if (!dir) {
        dir = { type: 'dir', name: dirName, path: dirPath, children: [] };
        current.push(dir);
      }

      current = dir.children;
    }

    current.push({ type: 'file', name: file.name, info: file });
  }

  function sortEntries(entries: TreeEntry[]): void {
    entries.sort((a, b) => {
      if (a.type !== b.type) return a.type === 'dir' ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const entry of entries) {
      if (entry.type === 'dir') sortEntries(entry.children);
    }
  }

  sortEntries(root);
  return root;
}

function treeHasMatch(entry: TreeEntry, filter: string): boolean {
  if (entry.type === 'file') {
    return entry.info.path.toLowerCase().includes(filter);
  }
  return entry.children.some((child) => treeHasMatch(child, filter));
}


function FileTreeEntries({
  entries,
  depth,
  collapsedDirs,
  onToggleDir,
  activeIndex,
  onFileClick,
  filter,
}: {
  entries: TreeEntry[];
  depth: number;
  collapsedDirs: Set<string>;
  onToggleDir: (path: string) => void;
  activeIndex: number | null;
  onFileClick: (index: number) => void;
  filter: string;
}) {
  const lowerFilter = filter.toLowerCase();

  return (
    <>
      {entries.map((entry) => {
        if (entry.type === 'dir') {
          if (lowerFilter && !treeHasMatch(entry, lowerFilter)) return null;
          const collapsed = collapsedDirs.has(entry.path);

          return (
            <div key={entry.path}>
              <button
                className="appearance-none border-none bg-transparent text-text-secondary flex items-center gap-[5px] w-full py-1 px-2.5 font-sans text-[0.78rem] font-semibold cursor-pointer select-none transition-[background] duration-100 ease-in-out hover:bg-surface-hover"
                style={{
                  paddingInlineStart: `${FILE_TREE_INDENT_BASE + depth * FILE_TREE_INDENT_STEP}px`,
                }}
                onClick={() => onToggleDir(entry.path)}
                type="button"
              >
                <span
                  className={`inline-block w-3.5 text-center text-[0.65rem] text-text-muted transition-transform duration-150 ease-in-out shrink-0 ${collapsed ? '-rotate-90' : ''}`}
                >
                  &#9662;
                </span>
                <span className="whitespace-nowrap overflow-hidden text-ellipsis">
                  {entry.name}
                </span>
              </button>
              {!collapsed && (
                <FileTreeEntries
                  entries={entry.children}
                  depth={depth + 1}
                  collapsedDirs={collapsedDirs}
                  onToggleDir={onToggleDir}
                  activeIndex={activeIndex}
                  onFileClick={onFileClick}
                  filter={filter}
                />
              )}
            </div>
          );
        }

        if (lowerFilter && !entry.info.path.toLowerCase().includes(lowerFilter)) {
          return null;
        }

        const active = entry.info.patchIndex === activeIndex;

        return (
          <button
            key={entry.info.path}
            className={`appearance-none border-none bg-transparent flex items-center gap-1.5 w-full py-[3px] px-2.5 font-mono text-[0.72rem] cursor-pointer select-none transition-[background,color] duration-100 ease-in-out ${active ? 'bg-accent-bg text-text-primary' : 'text-text-secondary hover:bg-surface-hover hover:text-text-primary'}`}
            style={{
              paddingInlineStart: `${FILE_TREE_INDENT_BASE + depth * FILE_TREE_INDENT_STEP}px`,
            }}
            onClick={() => onFileClick(entry.info.patchIndex)}
            type="button"
          >
            <span className="whitespace-nowrap overflow-hidden text-ellipsis">
              {entry.name}
            </span>
            <span className="ml-auto flex gap-[5px] text-[0.65rem] shrink-0">
              {entry.info.additions > 0 && (
                <span className="text-stat-add">+{entry.info.additions}</span>
              )}
              {entry.info.deletions > 0 && (
                <span className="text-stat-del">-{entry.info.deletions}</span>
              )}
            </span>
          </button>
        );
      })}
    </>
  );
}

export default function App() {
  const [stack, setStack] = useState<StackResponse | null>(null);
  const [selection, setSelection] = useState<DiffSelection | null>(null);
  const [diff, setDiff] = useState<DiffResponse | null>(null);
  const [workingPatch, setWorkingPatch] = useState<string | null>(null);
  const [isStackLoading, setIsStackLoading] = useState(true);
  const [isDiffLoading, setIsDiffLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diffStyle, setDiffStyle] = useState<DiffStyle>('unified');
  const [collapsedDirs, setCollapsedDirs] = useState<Set<string>>(new Set());
  const [fileFilter, setFileFilter] = useState('');
  const [activeFileIndex, setActiveFileIndex] = useState<number | null>(null);
  const fileRefs = useRef<Record<number, HTMLDivElement | null>>({});

  useEffect(() => {
    let cancelled = false;

    const loadStack = async () => {
      setIsStackLoading(true);
      setError(null);
      try {
        const data = await fetchJSON<StackResponse>('/api/stack');
        if (cancelled) return;

        setStack(data);
        if (data.branches.length === 0) {
          setSelection(null);
          return;
        }

        const firstBranch = data.branches[0];
        if (!firstBranch) {
          setSelection(null);
          return;
        }

        const preferred =
          data.branches.find((branch) => branch.name === data.currentBranch)?.name ??
          firstBranch.name;
        setSelection({ type: 'branch', name: preferred });
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load stack');
        }
      } finally {
        if (!cancelled) setIsStackLoading(false);
      }
    };

    void loadStack();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!selection) {
      setDiff(null);
      setWorkingPatch(null);
      return;
    }

    let cancelled = false;

    const loadDiff = async () => {
      setIsDiffLoading(true);
      setError(null);
      try {
        if (selection.type === 'working') {
          const data = await fetchJSON<WorkingDiffResponse>('/api/diff/working');
          if (!cancelled) {
            setWorkingPatch(data.patch);
            setDiff(null);
          }
        } else {
          const data = await fetchJSON<DiffResponse>(
            `/api/diff?branch=${encodeURIComponent(selection.name)}`,
          );
          if (!cancelled) {
            setDiff(data);
            setWorkingPatch(null);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load diff');
          setDiff(null);
          setWorkingPatch(null);
        }
      } finally {
        if (!cancelled) setIsDiffLoading(false);
      }
    };

    void loadDiff();
    return () => { cancelled = true; };
  }, [selection]);

  useEffect(() => {
    setCollapsedDirs(new Set());
    setFileFilter('');
    setActiveFileIndex(null);
    fileRefs.current = {};
  }, [selection]);

  const diffOptions = useMemo<PatchDiffProps<undefined>['options']>(
    () => ({
      diffStyle,
      diffIndicators: 'classic',
      hunkSeparators: 'metadata',
      theme: 'pierre-dark',
      themeType: 'dark',
      overflow: 'scroll',
      lineDiffType: 'word',
      unsafeCSS: '[data-diffs-header] { position: sticky; top: 0; z-index: 10; }',
    }),
    [diffStyle],
  );

  const activePatch = selection?.type === 'working' ? workingPatch : diff?.patch;

  const diffPatches = useMemo(
    () => splitPatchIntoFiles(activePatch ?? ''),
    [activePatch],
  );

  const fileInfos = useMemo(
    () => diffPatches.map((patch, i) => parseFileInfo(patch, i)),
    [diffPatches],
  );

  const fileTree = useMemo(
    () => buildFileTree(fileInfos),
    [fileInfos],
  );

  const toggleDir = useCallback((path: string) => {
    setCollapsedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const scrollToFile = useCallback((index: number) => {
    setActiveFileIndex(index);
    fileRefs.current[index]?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, []);

  const branches = stack?.branches ?? [];

  const parentIndexMap = useMemo(() => {
    const map = new Map<string, number>();
    branches.forEach((b, i) => map.set(b.name, i));
    return map;
  }, [branches]);

  const lastChildIndexMap = useMemo(() => {
    const map = new Map<number, number>();
    for (let i = 0; i < branches.length; i++) {
      const parentIdx = parentIndexMap.get(branches[i]!.parent);
      if (parentIdx !== undefined) {
        map.set(parentIdx, i);
      }
    }
    return map;
  }, [branches, parentIndexMap]);

  return (
    <main className="relative h-screen grid grid-cols-[280px_1fr] animate-fade-in max-[960px]:grid-cols-1 max-[960px]:grid-rows-[auto_1fr] overflow-hidden">
      <section className="border-r border-border bg-surface overflow-hidden flex flex-col">
        <div className="px-[1.15rem] h-[60px] shrink-0 flex flex-col justify-center border-b border-border">
          <p className="font-mono text-[0.68rem] font-medium uppercase tracking-[0.13em] text-accent m-0 mb-[0.3rem]">
            Shortcake
          </p>
          <h1>Stack Diff Explorer</h1>
        </div>

        {isStackLoading ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">Loading stack…</p>
        ) : null}

        {!isStackLoading && branches.length === 0 ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">
            No tracked branches found in this repository.
          </p>
        ) : null}

        <div
          className="relative flex flex-col gap-0 p-1.5 overflow-y-auto overflow-x-clip flex-1 max-[960px]:max-h-[200px]"
          role="list"
          aria-label="Tracked stack branches"
        >
          <button
            className={`relative appearance-none rounded-md py-[5px] px-[7px] mx-[8px] mb-1 text-left text-text-primary cursor-pointer transition-[background] duration-150 ease-in-out border-none ${selection?.type === 'working' ? 'bg-accent-bg' : 'bg-transparent hover:bg-surface-hover'}`}
            onClick={() => setSelection({ type: 'working' })}
            type="button"
          >
            <span className="relative z-[2] flex items-center gap-[7px]">
              <span className="text-[0.82rem] font-semibold whitespace-nowrap overflow-hidden text-ellipsis">
                Working Changes
              </span>
              <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-text-muted bg-surface-hover border border-border px-[5px] py-px rounded-full shrink-0 leading-[1.5]">
                git diff
              </span>
            </span>
          </button>

          {branches.length > 0 && (
            <div className="border-t border-border mx-2 my-1" />
          )}

          {branches.map((branch, index) => {
            const active = selection?.type === 'branch' && branch.name === selection.name;
            const branchPadding =
              STACK_CARD_INDENT_BASE + branch.depth * STACK_CARD_INDENT_STEP;
            const parentIndex = parentIndexMap.get(branch.parent) ?? -1;
            const lastChildIdx = lastChildIndexMap.get(index);

            return (
              <React.Fragment key={branch.name}>
                <button
                  className={`relative appearance-none rounded-md py-[5px] px-[7px] text-left text-text-primary cursor-pointer transition-[background] duration-150 ease-in-out border-none ${active ? 'bg-accent-bg' : 'bg-transparent hover:bg-surface-hover'}`}
                  style={{
                    anchorName: `--branch-${index}`,
                    marginInlineStart: `${branchPadding}px`,
                    marginInlineEnd: '8px',
                  } as React.CSSProperties}
                  onClick={() => setSelection({ type: 'branch', name: branch.name })}
                  type="button"
                >
                  <span className="relative z-[2] flex items-center gap-[7px]">
                    <span className="text-[0.88rem] font-semibold whitespace-nowrap overflow-hidden text-ellipsis">
                      {branch.name}
                    </span>
                    {branch.isCurrent && (
                      <span className="font-mono text-[0.58rem] font-medium uppercase tracking-[0.05em] text-accent bg-[rgba(52,211,153,0.1)] border border-[rgba(52,211,153,0.18)] ml-1.5 px-[5px] py-px rounded-full shrink-0 leading-[1.5]">
                        current
                      </span>
                    )}
                  </span>
                </button>
                {lastChildIdx !== undefined && (
                  <div
                    aria-hidden
                    className="stack-guide-vertical"
                    style={{
                      '--from': `--branch-${index}`,
                      '--to': `--branch-${lastChildIdx}`,
                      left: `${STACK_GUIDE_OFFSET + branch.depth * STACK_GUIDE_STEP}px`,
                    } as React.CSSProperties}
                  />
                )}
                {branch.depth > 0 && parentIndex >= 0 && (
                  <div
                    aria-hidden
                    className="stack-guide-horizontal"
                    style={{
                      '--at': `--branch-${index}`,
                      left: `${STACK_GUIDE_OFFSET + (branch.depth - 1) * STACK_GUIDE_STEP}px`,
                      width: '10px',
                    } as React.CSSProperties}
                  />
                )}
              </React.Fragment>
            );
          })}
        </div>
      </section>

      <section className="bg-surface overflow-hidden flex flex-col min-w-0">
        <header className="px-[1.15rem] h-[60px] shrink-0 border-b border-border flex justify-between items-center gap-4">
          <div>
            <p className="font-mono text-[0.68rem] font-medium uppercase tracking-[0.13em] text-accent m-0 mb-[0.3rem]">
              Diff
            </p>
            <h2>
              {selection?.type === 'working' ? (
                'Uncommitted changes'
              ) : diff ? (
                <>
                  {diff.branch}{' '}
                  <span className="text-text-muted font-normal">&rarr;</span>{' '}
                  {diff.parent}
                </>
              ) : (
                'Select a branch'
              )}
            </h2>
          </div>

          <div className="flex items-center gap-2">
            {!isDiffLoading && diffPatches.length > 0 && (
              <span className="font-mono text-[0.68rem] text-text-secondary bg-surface-hover border border-border px-2 py-[3px] rounded-full whitespace-nowrap">
                {diffPatches.length} file{diffPatches.length === 1 ? '' : 's'}
              </span>
            )}
            <div
              className="flex bg-white/4 border border-border rounded-md p-0.5"
              role="group"
              aria-label="Diff layout"
            >
              <button
                className={`appearance-none border-none rounded-[6px] font-mono text-[0.7rem] tracking-[0.02em] px-2.5 py-1 cursor-pointer transition-[color,background] duration-[120ms] ease-in-out ${diffStyle === 'unified' ? 'text-text-primary bg-surface-active' : 'bg-transparent text-text-muted hover:text-text-secondary'}`}
                onClick={() => setDiffStyle('unified')}
                type="button"
              >
                Unified
              </button>
              <button
                className={`appearance-none border-none rounded-[6px] font-mono text-[0.7rem] tracking-[0.02em] px-2.5 py-1 cursor-pointer transition-[color,background] duration-[120ms] ease-in-out ${diffStyle === 'split' ? 'text-text-primary bg-surface-active' : 'bg-transparent text-text-muted hover:text-text-secondary'}`}
                onClick={() => setDiffStyle('split')}
                type="button"
              >
                Split
              </button>
            </div>
          </div>
        </header>

        {error ? (
          <p className="m-[1.15rem] text-danger text-[0.88rem]">{error}</p>
        ) : null}

        {isDiffLoading ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">Loading diff…</p>
        ) : null}

        {!isDiffLoading && activePatch !== undefined && activePatch !== null && activePatch.trim() === '' ? (
          <p className="m-[1.15rem] text-text-muted text-[0.88rem]">
            {selection?.type === 'working'
              ? 'No uncommitted changes.'
              : 'No file differences between this branch and its parent.'}
          </p>
        ) : null}

        {!isDiffLoading && activePatch && activePatch.trim() !== '' && diffPatches.length === 0 ? (
          <p className="m-[1.15rem] text-danger text-[0.88rem]">
            Could not render this diff patch.
          </p>
        ) : null}

        {!isDiffLoading && activePatch && diffPatches.length > 0 && (
          <div className="flex flex-1 min-h-0">
            <aside className="w-[260px] min-w-[260px] border-r border-border flex flex-col overflow-hidden max-[1100px]:hidden">
              <div className="px-3.5 py-2.5 text-[0.8rem] font-semibold text-text-secondary border-b border-border">
                Files changed ({fileInfos.length})
              </div>
              <div className="px-2.5 py-2 border-b border-border">
                <input
                  className="w-full appearance-none border border-border rounded-md bg-surface-hover text-text-primary font-mono text-[0.72rem] px-[9px] py-1.5 outline-none transition-[border-color] duration-150 ease-in-out focus:border-border-strong placeholder:text-text-muted"
                  type="text"
                  placeholder="Filter files..."
                  value={fileFilter}
                  onChange={(e) => setFileFilter(e.target.value)}
                />
              </div>
              <div className="flex-1 overflow-y-auto py-1">
                <FileTreeEntries
                  entries={fileTree}
                  depth={0}
                  collapsedDirs={collapsedDirs}
                  onToggleDir={toggleDir}
                  activeIndex={activeFileIndex}
                  onFileClick={scrollToFile}
                  filter={fileFilter}
                />
              </div>
            </aside>

            <div className="diff-content flex-1 min-w-0 overflow-auto">
              {diffPatches.map((patch, index) => (
                  <div
                    className={index > 0 ? 'border-t border-border' : undefined}
                    key={`${selection?.type === 'working' ? 'working' : diff?.branch}-${index}`}
                    ref={(el) => { fileRefs.current[index] = el; }}
                  >
                    <PatchDiff patch={patch} options={diffOptions} />
                  </div>
              ))}
            </div>
          </div>
        )}
      </section>
    </main>
  );
}
