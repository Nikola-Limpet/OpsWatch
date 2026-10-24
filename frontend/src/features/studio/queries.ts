import {
  skipToken,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useEffect } from "react";
import { action, request } from "../../api";
import type { Report, SessionPage, StudioOverview } from "./model";
import { isActive } from "./model";
export const studioKeys = {
  overview: ["studio", "overview"] as const,
  session: (id: string | null) => ["studio", "session", id] as const,
  report: (id: string | null) => ["studio", "report", id] as const,
  collection: (collection: string) =>
    ["studio", "collection", collection] as const,
};
interface CollectionItem {
  id: string;
  updated: number;
  feedback?: "helpful" | "unhelpful" | null;
}
interface CollectionState<T> {
  items: T[];
  cursor: string | null;
  hasMore: boolean;
  loadedOlder: boolean;
}
function mergeRecords<T extends CollectionItem>(
  previous: T[],
  incoming: T[],
): T[] {
  const records = new Map(previous.map((item) => [item.id, item]));
  for (const item of incoming) records.set(item.id, item);
  return [...records.values()].sort(
    (a, b) => b.updated - a.updated || b.id.localeCompare(a.id),
  );
}
export function useStudioCollection<T extends CollectionItem>(
  collection: "sessions" | "reports" | "problems",
  initial: T[] | undefined,
  initialHasMore = false,
) {
  const client = useQueryClient();
  const state = useQuery<CollectionState<T>>({
    queryKey: studioKeys.collection(collection),
    queryFn: skipToken,
    enabled: false,
    initialData: {
      items: initial ?? [],
      cursor: initial?.at(-1)?.id ?? null,
      hasMore: initialHasMore,
      loadedOlder: false,
    },
  });
  const current = state.data ?? {
    items: initial ?? [],
    cursor: initial?.at(-1)?.id ?? null,
    hasMore: initialHasMore,
    loadedOlder: false,
  };
  useEffect(() => {
    if (!initial) return;
    client.setQueryData<CollectionState<T>>(
      studioKeys.collection(collection),
      (previous) => {
        const items = mergeRecords(previous?.items ?? [], initial);
        return {
          items,
          cursor: previous?.loadedOlder
            ? previous.cursor
            : (items.at(-1)?.id ?? null),
          hasMore: previous?.loadedOlder ? previous.hasMore : initialHasMore,
          loadedOlder: previous?.loadedOlder ?? false,
        };
      },
    );
  }, [client, collection, initial, initialHasMore]);
  const load = useMutation({
    retry: false,
    mutationFn: () =>
      studioAction<{
        items: T[];
        has_more: boolean;
        next_before: string | null;
      }>("collection_read", {
        collection,
        limit: 20,
        ...(current.cursor ? { before: current.cursor } : {}),
      }),
    onSuccess: (page) =>
      client.setQueryData<CollectionState<T>>(
        studioKeys.collection(collection),
        (previous) => ({
          items: mergeRecords(previous?.items ?? [], page.items),
          cursor: page.next_before,
          hasMore: page.has_more,
          loadedOlder: true,
        }),
      ),
  });
  return {
    items: current.items,
    hasMore: current.loadedOlder ? current.hasMore : initialHasMore,
    loadOlder: () => {
      if (!load.isPending) load.mutate();
    },
    isLoading: load.isPending,
    error: load.error,
  };
}
export function studioAction<T>(
  name: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  return action<T>("/api/studio/actions", { action: name, payload });
}
export function useReloadStudio() {
  const client = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: () => request<StudioOverview>("/api/studio"),
    onSuccess: (data) => client.setQueryData(studioKeys.overview, data),
  });
}
export function useStudio() {
  return useQuery({
    queryKey: studioKeys.overview,
    queryFn: () => request<StudioOverview>("/api/studio"),
    refetchInterval: 5000,
  });
}
export function useStudioAction<T = unknown>() {
  const client = useQueryClient();
  return useMutation({
    retry: false,
    mutationFn: ({
      name,
      payload,
    }: {
      name: string;
      payload: Record<string, unknown>;
    }) => studioAction<T>(name, payload),
    onSuccess: (result, input) => {
      if (
        input.name === "feedback" &&
        ["report", "problem"].includes(String(input.payload.subject_type))
      ) {
        const collection =
          input.payload.subject_type === "report" ? "reports" : "problems";
        client.setQueryData<CollectionState<CollectionItem>>(
          studioKeys.collection(collection),
          (previous) =>
            previous
              ? {
                  ...previous,
                  items: previous.items.map((item) =>
                    item.id === input.payload.subject_id
                      ? {
                          ...item,
                          feedback: input.payload.rating as
                            | "helpful"
                            | "unhelpful",
                        }
                      : item,
                  ),
                }
              : previous,
        );
      }
      if (result && typeof result === "object") {
        for (const collection of ["sessions", "reports", "problems"] as const) {
          const item = (result as Record<string, unknown>)[
            collection.slice(0, -1)
          ];
          if (
            item &&
            typeof item === "object" &&
            "id" in item &&
            "updated" in item
          )
            client.setQueryData<CollectionState<CollectionItem>>(
              studioKeys.collection(collection),
              (previous) =>
                previous
                  ? {
                      ...previous,
                      items: mergeRecords(previous.items, [
                        item as CollectionItem,
                      ]),
                    }
                  : previous,
            );
        }
      }
      return client.invalidateQueries({ queryKey: ["studio"] });
    },
  });
}
export function useSession(id: string | null) {
  return useInfiniteQuery({
    queryKey: studioKeys.session(id),
    retry: false,
    enabled: Boolean(id),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      studioAction<SessionPage>("session_read", {
        session_id: id,
        limit: 20,
        ...(pageParam ? { before: pageParam } : {}),
      }),
    getNextPageParam: (last) =>
      last.has_more && last.next_before ? last.next_before : undefined,
    refetchInterval: 4000,
  });
}
export function useReport(id: string | null) {
  return useQuery({
    queryKey: studioKeys.report(id),
    retry: false,
    enabled: Boolean(id),
    queryFn: () =>
      studioAction<{ report: Report }>("report_read", { report_id: id }),
    refetchInterval: (query) =>
      query.state.data && isActive(query.state.data.report.status)
        ? 4000
        : false,
  });
}
