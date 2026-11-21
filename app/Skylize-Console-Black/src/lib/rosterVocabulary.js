// Single dictionary for the two distinct "lifecycle" concepts this app shows.
// OD6 (docs/decisions/2026-11-07_owner_rulings.md): one computation path per
// fact, never two differently-sourced numbers both claiming "governed".
// Import these constants instead of re-typing the words as free text.
//
// GOVERNED_LABEL -- the live registry fact: GET /api/v1/agents, backed by
// ALL_MVP_CONTRACTS (src/skylize/contracts/mvp/__init__.py). An agent this
// label describes has a registered AgentContract and can execute today.
// Every on-screen "governed" COUNT must derive from `agentsLive`, never from
// the agentNetworkData fixture.
export const GOVERNED_LABEL = 'GOVERNED';

// ROSTER_CONTRACT_BACKED_LABEL / ROSTER_PLACEHOLDER_LABEL -- provenance of a
// node inside the agentNetworkData fixture (RosterLifecycle in
// agent-network.types.ts), an illustrative 151-role org chart. A
// "contract-backed" fixture node happens to carry a real AgentContract's
// budget; a "placeholder" one does not. This is NOT the live registry count
// and is deliberately never labelled "governed" on screen, so it can't be
// read as competing with GOVERNED_LABEL.
export const ROSTER_CONTRACT_BACKED_LABEL = 'CONTRACT-BACKED';
export const ROSTER_PLACEHOLDER_LABEL = 'PLACEHOLDER';
