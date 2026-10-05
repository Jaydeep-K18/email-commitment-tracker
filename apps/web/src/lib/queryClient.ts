import { QueryCache, QueryClient } from "@tanstack/react-query";

import { ApiError } from "./api";
import { keys } from "./queries";

export function createQueryClient() {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      // A 401 anywhere means the session ended: refresh it, and the route
      // guard sends the user to sign in.
      onError: (error) => {
        if (error instanceof ApiError && error.status === 401) {
          void client.invalidateQueries({ queryKey: keys.session });
        }
      },
    }),
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        refetchOnWindowFocus: true,
        retry: (failures, error) => !(error instanceof ApiError && error.status < 500) && failures < 2,
      },
    },
  });
  return client;
}
