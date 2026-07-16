<script lang="ts">
	import { browser } from '$app/environment';
	import { getNotes } from '$lib/api';
	import '$lib/styles/monitor-compact.css';
	import CompactServerRow from '$lib/components/CompactServerRow.svelte';
	import type { Note, ServerState } from '$lib/types';
	import {
		COMPACT_GPU_BANK_SIZE,
		compactGpuBankCount,
		compactGpuBankSlots
	} from '$lib/utils/compactGpuMatrix';

	type CompactHoldCue = {
		owner: string;
		remaining: string;
		memo: string;
	};

	type CompactHoldLoadResult = {
		notesByServer: Map<number, Note[]>;
		failedServerIds: Set<number>;
	};

	type CompactPopoverItem = {
		gpuIndex: number;
		stateLabel: string;
		ownersLabel: string;
		holdLabel: string;
	};

	type CompactPopover = {
		serverId: number;
		serverName: string;
		item: CompactPopoverItem;
		left: number;
		top: number;
		trigger: HTMLElement | null;
	};

	let {
		servers,
		heldGpuIndicesByServer = undefined,
		onOpenFull = () => {}
	}: {
		servers: ServerState[];
		heldGpuIndicesByServer?: ReadonlyMap<number, ReadonlySet<number>>;
		onOpenFull?: (serverId: number) => void;
	} = $props();

	const TOOLTIP_WIDTH_ESTIMATE = 240;
	const TOOLTIP_HEIGHT_ESTIMATE = 132;
	const TOOLTIP_VIEWPORT_MARGIN = 12;
	const HOLD_REFRESH_MS = 30_000;

	let activeBankIndex = $state(0);
	let activeTooltip = $state<CompactPopover | null>(null);
	let nowMs = $state(Date.now());
	let compactHoldNotesByServer = $state(new Map<number, Note[]>());
	let compactHoldLoadErrors = $state(new Set<number>());
	let holdNotesCache = new Map<number, Note[]>();

	const serverIdSignature = $derived(servers.map((server) => server.server_id).join(','));
	const bankCount = $derived(compactGpuBankCount(servers));
	const bankOptions = $derived.by(() => Array.from({ length: bankCount }, (_, index) => index));
	const bankStart = $derived(activeBankIndex * COMPACT_GPU_BANK_SIZE);
	const activeBankHasHardware = $derived.by(() =>
		servers.some((server) => compactGpuBankSlots(server.gpus, activeBankIndex).some(Boolean))
	);
	const fetchedHoldCuesByServer = $derived.by(() => {
		const cuesByServer = new Map<number, Map<number, CompactHoldCue[]>>();

		for (const server of servers) {
			const notes = compactHoldNotesByServer.get(server.server_id) ?? [];
			const cuesByGpu = new Map<number, CompactHoldCue[]>();

			for (const note of notes) {
				if (note.kind !== 'hold' || !noteVisible(note, nowMs)) continue;

				const gpuIndices = holdGpuIndices(note);
				if (gpuIndices.length === 0) continue;

				const cue: CompactHoldCue = {
					owner: note.username,
					remaining: noteRemainingText(note, nowMs),
					memo: note.content
				};

				for (const gpuIndex of gpuIndices) {
					cuesByGpu.set(gpuIndex, [...(cuesByGpu.get(gpuIndex) ?? []), cue]);
				}
			}

			if (cuesByGpu.size > 0) {
				cuesByServer.set(server.server_id, cuesByGpu);
			}
		}

		return cuesByServer;
	});
	const fetchedHeldGpuIndicesByServer = $derived.by(() => {
		const heldByServer = new Map<number, ReadonlySet<number>>();
		for (const [serverId, cuesByGpu] of fetchedHoldCuesByServer) {
			heldByServer.set(serverId, new Set(cuesByGpu.keys()));
		}
		return heldByServer;
	});
	const resolvedHeldGpuIndicesByServer = $derived.by(() => {
		const resolved = new Map<number, ReadonlySet<number>>();
		for (const { server_id: serverId } of servers) {
			const heldGpuIndices = heldGpuIndicesByServer?.get(serverId) ?? fetchedHeldGpuIndicesByServer.get(serverId);
			if (heldGpuIndices) resolved.set(serverId, heldGpuIndices);
		}
		return resolved;
	});

	function parseServerIdSignature(signature: string): number[] {
		if (!signature) return [];
		return signature
			.split(',')
			.map((value) => Number(value))
			.filter((value) => Number.isInteger(value) && value > 0);
	}

	function parseNoteTime(iso: string | null): number | null {
		if (!iso) return null;
		const ms = Date.parse(iso);
		return Number.isNaN(ms) ? null : ms;
	}

	function noteVisible(note: Note, nowMs: number): boolean {
		const expiresAtMs = parseNoteTime(note.expires_at);
		return expiresAtMs === null || expiresAtMs > nowMs;
	}

	function noteRemainingText(note: Note, nowMs: number): string {
		const expiresAtMs = parseNoteTime(note.expires_at);
		if (expiresAtMs === null) return '';
		const remainingMs = expiresAtMs - nowMs;
		if (remainingMs <= 0) return '만료됨';
		const seconds = Math.ceil(remainingMs / 1000);
		if (seconds < 60) return `${seconds}초`;
		const minutes = Math.ceil(seconds / 60);
		if (minutes < 60) return `${minutes}분`;
		const hours = Math.ceil(minutes / 60);
		if (hours < 48) return `${hours}시간`;
		return `${Math.ceil(hours / 24)}일`;
	}

	function holdGpuIndices(note: Note): number[] {
		const gpuIndices: number[] = [];
		for (const value of note.gpu_indices) {
			if (!Number.isInteger(value) || value < 0 || gpuIndices.includes(value)) continue;
			gpuIndices.push(value);
		}
		return gpuIndices;
	}

	async function loadHoldNotes(
		serverIds: readonly number[],
		previousNotes: ReadonlyMap<number, Note[]>
	): Promise<CompactHoldLoadResult> {
		const outcomes = await Promise.all(
			serverIds.map(async (serverId) => {
				try {
					return {
						serverId,
						notes: await getNotes(serverId),
						failed: false
					};
				} catch {
					return {
						serverId,
						notes: previousNotes.get(serverId) ?? [],
						failed: true
					};
				}
			})
		);
		const notesByServer = new Map<number, Note[]>();
		const failedServerIds = new Set<number>();
		for (const outcome of outcomes) {
			notesByServer.set(outcome.serverId, outcome.notes);
			if (outcome.failed) failedServerIds.add(outcome.serverId);
		}
		return { notesByServer, failedServerIds };
	}

	function clampTooltip(next: CompactPopover): CompactPopover {
		const left = Math.min(
			Math.max(next.left, TOOLTIP_VIEWPORT_MARGIN),
			window.innerWidth - TOOLTIP_WIDTH_ESTIMATE - TOOLTIP_VIEWPORT_MARGIN
		);
		const top = Math.min(
			Math.max(next.top, TOOLTIP_VIEWPORT_MARGIN),
			window.innerHeight - TOOLTIP_HEIGHT_ESTIMATE - TOOLTIP_VIEWPORT_MARGIN
		);
		return { ...next, left, top };
	}

	function updateTooltip(next: CompactPopover | null): void {
		if (!next) {
			activeTooltip = null;
			return;
		}
		activeTooltip = clampTooltip(next);
	}

	function closeTooltip({ restoreFocus = false }: { restoreFocus?: boolean } = {}): void {
		const currentTooltip = activeTooltip;
		activeTooltip = null;
		if (restoreFocus && currentTooltip?.trigger?.isConnected) {
			currentTooltip.trigger.focus();
		}
	}

	function openFull(serverId: number): void {
		closeTooltip();
		onOpenFull(serverId);
	}

	function selectBank(index: number): void {
		activeBankIndex = index;
		closeTooltip();
	}

	function handleWindowKeydown(event: KeyboardEvent): void {
		if (event.key !== 'Escape' || !activeTooltip) return;
		event.preventDefault();
		closeTooltip({ restoreFocus: true });
	}

	$effect(() => {
		if (activeBankIndex < bankCount) return;
		activeBankIndex = Math.max(0, bankCount - 1);
	});

	$effect(() => {
		if (!browser) return;
		const timer = window.setInterval(() => {
			nowMs = Date.now();
		}, HOLD_REFRESH_MS);
		return () => window.clearInterval(timer);
	});

	$effect(() => {
		if (!browser) return;
		let cancelled = false;
		let holdRefreshInFlight = false;
		const serverIdSnapshot = parseServerIdSignature(serverIdSignature);
		const refresh = async (): Promise<void> => {
			if (holdRefreshInFlight) return;
			holdRefreshInFlight = true;
			try {
				const result = await loadHoldNotes(serverIdSnapshot, holdNotesCache);
				if (cancelled) return;
				holdNotesCache = result.notesByServer;
				compactHoldNotesByServer = result.notesByServer;
				compactHoldLoadErrors = result.failedServerIds;
			} finally {
				holdRefreshInFlight = false;
			}
		};
		void refresh();
		const timer = window.setInterval(() => {
			void refresh();
		}, HOLD_REFRESH_MS);
		return () => {
			cancelled = true;
			window.clearInterval(timer);
		};
	});

	$effect(() => {
		if (!browser) return;
		const closeOnViewportChange = (): void => {
			closeTooltip();
		};
		const closeOnOutsidePointer = (event: PointerEvent): void => {
			if (!(event.target instanceof HTMLElement)) return;
			if (event.target.closest('[data-compact-trigger="true"]')) return;
			closeTooltip();
		};
		window.addEventListener('scroll', closeOnViewportChange, true);
		window.addEventListener('resize', closeOnViewportChange);
		window.addEventListener('pointerdown', closeOnOutsidePointer);
		return () => {
			window.removeEventListener('scroll', closeOnViewportChange, true);
			window.removeEventListener('resize', closeOnViewportChange);
			window.removeEventListener('pointerdown', closeOnOutsidePointer);
		};
	});
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<section class="compact-dashboard" aria-label="Compact GPU availability rack">
	{#if bankCount > 1 || compactHoldLoadErrors.size > 0}
		<div class="compact-dashboard__controls">
			{#if compactHoldLoadErrors.size > 0}
				<span class="compact-dashboard__hold-warning" role="status" title="기존 HOLD 표시를 유지하며 다시 확인합니다.">
					HOLD 정보 확인 지연
				</span>
			{/if}
			{#if bankCount > 1}
				<div class="compact-dashboard__bank-selector" role="group" aria-label="GPU bank selector">
					{#each bankOptions as bankIndex (bankIndex)}
						<button
							type="button"
							class="compact-dashboard__bank-button"
							class:is-active={bankIndex === activeBankIndex}
							aria-pressed={bankIndex === activeBankIndex}
							aria-current={bankIndex === activeBankIndex ? 'true' : undefined}
							onclick={() => selectBank(bankIndex)}
						>
							G{bankIndex * COMPACT_GPU_BANK_SIZE}-G{bankIndex * COMPACT_GPU_BANK_SIZE + COMPACT_GPU_BANK_SIZE - 1}
						</button>
					{/each}
				</div>
			{/if}
		</div>
	{/if}

	<div class="compact-dashboard__board" data-empty-bank={activeBankHasHardware ? 'false' : 'true'} role="list">
		<div class="compact-dashboard__column-header" aria-hidden="true">
			<span class="compact-dashboard__column-label">Server</span>
			{#each Array.from({ length: COMPACT_GPU_BANK_SIZE }) as _, offset (`compact-column-${bankStart + offset}`)}
				<span>G{bankStart + offset}</span>
			{/each}
		</div>

		{#each servers as server (server.server_id)}
			<CompactServerRow
				{server}
				bankIndex={activeBankIndex}
				heldGpuIndices={resolvedHeldGpuIndicesByServer.get(server.server_id)}
				holdCuesByGpu={fetchedHoldCuesByServer.get(server.server_id)}
				onOpenFull={openFull}
				onTooltipChange={updateTooltip}
			/>
		{/each}
	</div>

	{#if activeTooltip}
		<div
			class="compact-dashboard__tooltip"
			data-compact-popover="true"
			role="tooltip"
			aria-label={`${activeTooltip.serverName} GPU 상태 힌트`}
			style={`left: ${activeTooltip.left}px; top: ${activeTooltip.top}px;`}
		>
			<p class="compact-dashboard__tooltip-title">{activeTooltip.serverName}</p>
			<ul class="compact-dashboard__tooltip-list">
				<li class="compact-dashboard__tooltip-entry" data-held={activeTooltip.item.holdLabel ? 'true' : undefined}>
					<span class="compact-dashboard__tooltip-gpu">G{activeTooltip.item.gpuIndex}</span>
					<div class="compact-dashboard__tooltip-body">
						<div class="compact-dashboard__tooltip-meta">
							<span class="compact-dashboard__tooltip-state">{activeTooltip.item.stateLabel}</span>
							{#if activeTooltip.item.holdLabel}
								<span class="compact-dashboard__tooltip-hold">{activeTooltip.item.holdLabel}</span>
							{/if}
						</div>
						<span class="compact-dashboard__tooltip-users">{activeTooltip.item.ownersLabel}</span>
					</div>
				</li>
			</ul>
		</div>
	{/if}
</section>
