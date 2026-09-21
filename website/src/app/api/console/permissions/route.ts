// GET /api/console/permissions -> backend GET /api/v1/permissions/matrix, verbatim.
//
// The Permission-matrix console screen. Every field this proxies through is
// itself mechanically derived on the backend from an `ast` scan of the real
// `Depends(require_role(...))` / `Depends(require_any_role(...))` /
// `Depends(require_any_role_or_user(...))` call sites in
// `src/skylize/edge/routes/*.py` (see edge/permission_matrix.py) — this route
// adds no shaping of its own because there is nothing here to project away or
// rename: `route_group` is already the raw route-file name the owner-approved
// design calls for, and `access[role].read`/`.write` are already booleans.
//
// RBAC lives on the backend (`require_any_role_or_user("owner", "admin")`,
// permissions.py) and is enforced there, same as every other console read;
// this proxy adds only the console's own session gate (`consoleRoute`'s
// `requireAuth`, default true) and keeps the service API key server-side.

import { NextResponse } from "next/server";

import { skylizeFetch } from "@/lib/skylize/client";
import { consoleRoute } from "@/lib/skylize/handler";
import type { BackendPermissionMatrixResponse } from "@/lib/skylize/types";

export const GET = consoleRoute({
  method: "GET",
  handler: async () => {
    const matrix = await skylizeFetch<BackendPermissionMatrixResponse>(
      "/api/v1/permissions/matrix",
    );
    return NextResponse.json(matrix);
  },
});
