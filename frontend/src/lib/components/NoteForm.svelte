<script lang="ts">
  import type { Note } from '$lib/types';
  import { createNote } from '$lib/api';

	let {
		serverId,
		onCreated
	}: {
		serverId: number;
		onCreated: (note: Note) => void;
	} = $props();

	const DEFAULT_EXPIRY_MS = 24 * 60 * 60 * 1000;
	const ONE_HOUR_MS = 60 * 60 * 1000;
	const ONE_DAY_MS = 24 * 60 * 60 * 1000;
	const MIN_FUTURE_MS = 60 * 1000;

	let username = $state('');
	let sshPassword = $state('');
	let content  = $state('');
	let loading  = $state(false);
	let error    = $state('');
	let showPrecisePicker = $state(false);
	let nowMs = $state(Date.now());
	let expiresAtLocal = $state(defaultExpiryLocal());

	function pad(value: number): string {
		return String(value).padStart(2, '0');
	}

	function toLocalDateTimeValue(date: Date): string {
		return [
			date.getFullYear(),
			pad(date.getMonth() + 1),
			pad(date.getDate())
		].join('-') + `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
	}

	function defaultExpiryLocal(baseMs = Date.now()): string {
		return toLocalDateTimeValue(new Date(baseMs + DEFAULT_EXPIRY_MS));
	}

	function parseLocalDateTimeValue(value: string): Date | null {
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return null;
		return date;
	}

	function clampExpiry(date: Date): Date {
		const minMs = Date.now() + MIN_FUTURE_MS;
		return date.getTime() < minMs ? new Date(minMs) : date;
	}

	function shiftExpiry(deltaMs: number) {
		const base = parseLocalDateTimeValue(expiresAtLocal) ?? new Date(Date.now() + DEFAULT_EXPIRY_MS);
		const next = clampExpiry(new Date(base.getTime() + deltaMs));
		expiresAtLocal = toLocalDateTimeValue(next);
	}

	function formatExpiryAbsolute(date: Date): string {
		return new Intl.DateTimeFormat('ko-KR', {
			month: 'numeric',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit',
			hour12: false
		}).format(date);
	}

	function formatExpiryCompact(date: Date): string {
		return `${date.getMonth() + 1}/${date.getDate()} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
	}

	function formatRemaining(ms: number): string {
		const seconds = Math.max(0, Math.ceil(ms / 1000));
		if (seconds < 60) return `${seconds}초 남음`;

		const minutes = Math.ceil(seconds / 60);
		if (minutes < 60) return `${minutes}분 남음`;

		const hours = Math.ceil(minutes / 60);
		if (hours < 48) return `${hours}시간 남음`;

		const days = Math.ceil(hours / 24);
		return `${days}일 남음`;
	}

	const expiresAtDate = $derived(parseLocalDateTimeValue(expiresAtLocal));
	const minExpiryLocal = $derived(toLocalDateTimeValue(new Date(nowMs + MIN_FUTURE_MS)));
	const expiryCompactText = $derived.by(() => {
		if (!expiresAtDate) return '시간 선택';

		const diffMs = expiresAtDate.getTime() - nowMs;
		if (diffMs <= 0) return '시간 다시 선택';

		return `${formatRemaining(diffMs)} · ${formatExpiryCompact(expiresAtDate)}`;
	});
	const expirySummaryText = $derived.by(() => {
		if (!expiresAtDate) return '자동 삭제 시간을 확인하세요.';

		const diffMs = expiresAtDate.getTime() - nowMs;
		if (diffMs <= 0) return '현재보다 미래 시각을 선택하세요.';

		return `${formatExpiryAbsolute(expiresAtDate)} · ${formatRemaining(diffMs)}`;
	});

	async function handleSubmit() {
		if (!username.trim() || !sshPassword.trim() || !content.trim()) return;

		if (!expiresAtDate) {
			error = '자동 삭제 시간을 확인하세요.';
			return;
		}

		if (expiresAtDate.getTime() <= Date.now()) {
			error = '자동 삭제 시간은 현재보다 뒤여야 합니다.';
			return;
		}

		loading = true;
		error   = '';
		try {
			const note = await createNote(serverId, {
				username: username.trim(),
				ssh_password: sshPassword.trim(),
				content: content.trim(),
				expires_at: expiresAtDate.toISOString()
			});
			onCreated(note);
			content = '';
			expiresAtLocal = defaultExpiryLocal();
			showPrecisePicker = false;
		} catch (e) {
      error = e instanceof Error ? e.message : '작성 실패';
    } finally {
      loading = false;
    }
  }

	$effect(() => {
		const timer = setInterval(() => {
			nowMs = Date.now();
		}, 1000);

		return () => clearInterval(timer);
	});
</script>

<div class="note-form flex flex-col gap-1.5 pt-1.5">
  <div class="note-form-identity-row">
    <input
      type="text"
      placeholder="이름"
      bind:value={username}
      class="note-form-input note-form-input-half min-w-0 rounded-lg border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-xs text-white/80 placeholder:text-white/25 focus:outline-none focus:border-white/20"
    />
    <input
      type="password"
      placeholder="비밀번호"
      bind:value={sshPassword}
      class="note-form-input note-form-input-half min-w-0 rounded-lg border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-xs text-white/80 placeholder:text-white/25 focus:outline-none focus:border-white/20"
    />
  </div>

  <textarea
    placeholder="내용"
    bind:value={content}
    rows="2"
    class="note-form-textarea w-full rounded-lg border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-xs text-white/80 placeholder:text-white/25 focus:outline-none focus:border-white/20 resize-none"
  ></textarea>

  <div class="note-form-time-block">
    <div class="note-form-time-row">
      <span class="note-form-expiry-label">자동 삭제</span>

      <div class="note-form-expiry-controls">
        <button
          onclick={() => shiftExpiry(-ONE_HOUR_MS)}
          class="note-form-expiry-adjust"
          title="-1시간"
        >
          -1h
        </button>
        <button
          onclick={() => shiftExpiry(ONE_HOUR_MS)}
          class="note-form-expiry-adjust"
          title="+1시간"
        >
          +1h
        </button>
      </div>

      <span class="note-form-expiry-divider" aria-hidden="true">|</span>

      <div class="note-form-expiry-controls">
        <button
          onclick={() => shiftExpiry(-ONE_DAY_MS)}
          class="note-form-expiry-adjust"
          title="-1일"
        >
          -1d
        </button>
        <button
          onclick={() => shiftExpiry(ONE_DAY_MS)}
          class="note-form-expiry-adjust"
          title="+1일"
        >
          +1d
        </button>
      </div>

      <button
        onclick={() => { showPrecisePicker = !showPrecisePicker; }}
        class="note-form-expiry-toggle"
      >
        {showPrecisePicker ? '직접 설정 닫기' : '시간 직접 설정'}
      </button>
      <button
        onclick={handleSubmit}
        disabled={loading}
        class="note-form-submit-primary"
      >
        {#if loading}
          <span class="inline-block h-3.5 w-3.5 rounded-full border border-white/30 border-t-white/90 animate-spin"></span>
        {:else}
          작성
        {/if}
      </button>
    </div>

    <div class="note-form-expiry-summary-row">
      <span class="note-form-expiry-summary-label">삭제 예정</span>
      <span class="note-form-expiry-summary">{expirySummaryText}</span>
    </div>
  </div>

  {#if showPrecisePicker}
    <div class="note-form-precision">
      <input
        type="datetime-local"
        bind:value={expiresAtLocal}
        min={minExpiryLocal}
        class="note-form-input note-form-precision-input min-w-0 rounded-lg border border-white/8 bg-white/[0.04] px-2.5 py-1.5 text-xs text-white/80 focus:outline-none focus:border-white/20"
      />
      <span class="note-form-precision-meta">{expirySummaryText}</span>
    </div>
  {/if}

  <div class="note-form-footer flex items-center justify-between gap-2">
    {#if error}
      <span class="text-xs text-red-400">{error}</span>
    {:else}
      <span></span>
    {/if}
  </div>
</div>
