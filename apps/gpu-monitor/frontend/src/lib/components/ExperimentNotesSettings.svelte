<script lang="ts">
	import { onMount } from 'svelte';
	import type { ExperimentNotes } from '$lib/utils/experimentNotes';

	let {
		settings,
		loadError,
		onRetry,
		onSave,
		onClose
	}: {
		settings: ExperimentNotes | null;
		loadError: string;
		onRetry: () => void;
		onSave: (enabled: boolean, password: string) => Promise<void>;
		onClose: () => void;
	} = $props();

	let dialog: HTMLDialogElement;
	let requestedEnabled = $state<boolean | null>(null);
	let password = $state('');
	let saving = $state(false);
	let saveError = $state('');
	let saved = $state(false);
	const enabled = $derived(requestedEnabled ?? settings?.enabled ?? false);
	const unavailable = $derived(!settings || (!settings.configured && !settings.enabled));
	const canSave = $derived(!unavailable && !saving && enabled !== settings?.enabled && Boolean(password.trim()));

	onMount(() => dialog.showModal());

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (!canSave) return;
		saving = true;
		saved = false;
		saveError = '';
		try {
			await onSave(enabled, password);
			requestedEnabled = null;
			password = '';
			saved = true;
		} catch (error) {
			saveError = error instanceof Error ? error.message : '설정을 저장하지 못했습니다.';
		} finally {
			saving = false;
		}
	}
</script>

<dialog
	bind:this={dialog}
	aria-labelledby="experiment-notes-settings-title"
	oncancel={(event) => { if (saving) event.preventDefault(); }}
	onclose={onClose}
>
	<form onsubmit={save}>
		<h2 id="experiment-notes-settings-title">자동 실험 메모</h2>
		<p class="description">실행 파일명, 확인된 설정 파일명, GPU 수를 모델 API에 보내 작업을 요약합니다. 종료 예상 시각은 서버의 로그에서 계산합니다.</p>

		<label class="option">
			<span>자동 실험 메모 사용</span>
			<input
				type="checkbox"
				role="switch"
				checked={enabled}
				disabled={unavailable || saving}
				onchange={(event) => {
					requestedEnabled = event.currentTarget.checked;
					saved = false;
					saveError = '';
				}}
			/>
		</label>
		{#if settings}
			<p class="status">현재 상태 <strong>{!settings.configured ? 'API 미설정' : settings.enabled ? '켜짐' : '꺼짐'}</strong></p>
		{:else if loadError}
			<p class="error" role="alert">{loadError}</p>
			<button class="secondary retry" type="button" onclick={onRetry}>다시 확인</button>
		{:else}
			<p class="status" role="status">확인 중</p>
		{/if}

		<label class="password">
			관리자 패스워드
			<input type="password" autocomplete="current-password" bind:value={password} disabled={unavailable || saving} />
		</label>
		{#if saveError}
			<p class="error" role="alert">{saveError}</p>
		{:else if saved}
			<p class="status" role="status">설정을 저장했습니다.</p>
		{/if}
		<div class="actions">
			<button type="button" class="secondary" onclick={() => dialog.close()} disabled={saving}>닫기</button>
			<button type="submit" disabled={!canSave}>{saving ? '저장 중' : '저장'}</button>
		</div>
	</form>
</dialog>

<style>
	dialog {
		width: min(30rem, calc(100% - 2rem));
		max-height: calc(100% - 2rem);
		margin: auto;
		padding: 1.35rem;
		border: 1px solid var(--ops-border);
		border-radius: 0.85rem;
		background: var(--ops-card);
		color: var(--ops-fg);
		box-shadow: 0 16px 48px #0005;
	}

	dialog::backdrop {
		background: #0008;
	}

	h2 {
		font-size: 1rem;
		font-weight: 650;
	}

	.description, .status {
		color: var(--ops-muted-fg);
		font-size: 0.8rem;
		line-height: 1.5;
	}

	.description {
		margin: 0.4rem 0 1rem;
	}

	.option {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.6rem;
		padding: 0.65rem 0.75rem;
		border: 1px solid var(--ops-border);
		border-radius: 0.5rem;
		background: var(--ops-muted);
		font-size: 0.85rem;
	}

	.option input {
		appearance: none;
		width: 2.1rem;
		height: 1.22rem;
		padding: 0.12rem;
		border: 1px solid var(--ops-border);
		border-radius: 1rem;
		background: var(--ops-card);
		cursor: pointer;
	}

	.option input::before {
		content: '';
		display: block;
		width: 0.84rem;
		height: 0.84rem;
		border-radius: 50%;
		background: var(--ops-muted-fg);
	}

	.option input:checked {
		background: var(--ops-primary);
	}

	.option input:checked::before {
		margin-left: auto;
		background: var(--ops-primary-fg);
	}

	.status, .error {
		margin: 0.35rem 0 0;
	}

	.status strong {
		margin-left: 0.2rem;
		color: var(--ops-fg);
	}

	.error {
		color: var(--ops-danger);
		font-size: 0.8rem;
	}

	.password {
		display: grid;
		gap: 0.3rem;
		margin-top: 1rem;
		font-size: 0.8rem;
	}

	.password input {
		width: 100%;
		padding: 0.45rem 0.55rem;
		border: 1px solid var(--ops-border);
		border-radius: 0.45rem;
		background: var(--ops-muted);
		color: var(--ops-fg);
	}

	.actions {
		display: flex;
		justify-content: flex-end;
		gap: 0.45rem;
		margin-top: 1rem;
	}

	button {
		padding: 0.42rem 0.75rem;
		border: 1px solid transparent;
		border-radius: 0.45rem;
		background: var(--ops-primary);
		color: var(--ops-primary-fg);
		font-size: 0.8rem;
		font-weight: 600;
	}

	button.secondary {
		border-color: var(--ops-border);
		background: var(--ops-muted);
		color: var(--ops-fg);
	}

	button.retry {
		margin-top: 0.35rem;
	}

	button:disabled, input:disabled {
		cursor: default;
		opacity: 0.55;
	}

	button:focus-visible, input:focus-visible {
		outline: 2px solid var(--ops-ring);
		outline-offset: 2px;
	}
</style>
