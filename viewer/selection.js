"use strict";

/* Selection/delete-command state.
   Kept separate from table rendering so worker-2 can extend copy-only delete
   command behavior without editing app wiring or data loading boundaries. */
const cleanupSelected = new Map(); // path -> { path, bytes, owner, uid, mount, source }
