<script lang="ts">
	import type { ServerRecord } from '$lib/types';
	import { registerServer, updateServer, deleteServer, testConnectionRaw } from '$lib/api';

	let {
		open = $bindable(false),
		onClose,
		onSaved,
		editServer = $bindable<ServerRecord | null>(null)
	}: {
		open?: boolean;
		onClose: () => void;
		onSaved: () => void | Promise<void>;
		editServer?: ServerRecord | null;
	} = $props();

	// ── Form fields ─────────────────────────────────────────────────
	let name        = $state('');
	let host        = $state('');
	let port        = $state(22);
	let sshUser     = $state('');
	let authMode    = $state<'password' | 'key'>('password');
	let sshPassword = $state('');
	let sshKey      = $state('');
	let network     = $state<'internal' | 'external'>('internal');
	let adminPassword = $state('');

	// ── UI state ────────────────────────────────────────────────────
	let saving      = $state(false);
	let saveError   = $state('');
	let testStatus  = $state<'idle' | 'loading' | 'ok' | 'fail'>('idle');
	let testReason  = $state('');
	let deleteConfirm = $state(false);
	let deleting    = $state(false);

	function buildConnectionTestPayload() {
		return {
			...(editServer ? { server_id: editServer.id, admin_password: adminPassword.trim() } : {}),
			host: host.trim(),
			port: Number(port),
			ssh_user: sshUser.trim(),
			...(authMode === 'password' && sshPassword.trim() ? { ssh_password: sshPassword.trim() } : {}),
			...(authMode === 'key' && sshKey.trim() ? { ssh_private_key: sshKey.trim() } : {})
		};
	}

	// ── Sync form when panel opens ───────────────────────────────────
	$effect(() => {
		if (open) {
			if (editServer) {
				name    = editServer.name;
				host    = editServer.host;
				port    = editServer.port;
				sshUser = editServer.ssh_user;
				network = editServer.network;
				authMode = editServer.has_key ? 'key' : 'password';
				// Never pre-fill credentials
				sshPassword   = '';
				sshKey        = '';
				adminPassword = '';
			} else {
				name          = '';
				host          = '';
				port          = 22;
				sshUser       = '';
				authMode      = 'password';
				sshPassword   = '';
				sshKey        = '';
				network       = 'internal';
				adminPassword = '';
			}
			saveError     = '';
			testStatus    = 'idle';
			testReason    = '';
			deleteConfirm = false;
		}
	});

	// ── Save ─────────────────────────────────────────────────────────
	async function handleSave() {
		saveError = '';
		if (!name.trim())          { saveError = '서버 이름을 입력하세요.'; return; }
		if (!host.trim())          { saveError = '호스트를 입력하세요.'; return; }
		if (!sshUser.trim())       { saveError = 'SSH 유저를 입력하세요.'; return; }
		if (!adminPassword.trim()) { saveError = '관리자 패스워드를 입력하세요.'; return; }

		// On new server, credential is mandatory
		if (!editServer) {
			if (authMode === 'password' && !sshPassword.trim()) {
				saveError = 'SSH 비밀번호를 입력하세요.'; return;
			}
			if (authMode === 'key' && !sshKey.trim()) {
				saveError = 'SSH 키를 입력하세요.'; return;
			}
		}

		const payload = {
			name:     name.trim(),
			host:     host.trim(),
			port:     Number(port),
			ssh_user: sshUser.trim(),
			network,
			...(authMode === 'password' && sshPassword.trim() ? { ssh_password: sshPassword.trim() } : {}),
			...(authMode === 'key' && sshKey.trim() ? { ssh_private_key: sshKey.trim() } : {})
		};

		saving = true;
		try {
			if (editServer) {
				await updateServer(editServer.id, payload, adminPassword.trim());
			} else {
				await registerServer(payload, adminPassword.trim());
			}
			await onSaved();
			onClose();
		} catch (e) {
			saveError = e instanceof Error ? e.message : '저장 실패';
		} finally {
			saving = false;
		}
	}

	// ── Connection test ───────────────────────────────────────────────
	async function handleTest() {
		if (!adminPassword.trim()) { saveError = '관리자 패스워드를 입력하세요.'; return; }
		if (!host.trim() || !sshUser.trim()) {
			saveError  = '호스트와 SSH 유저를 먼저 입력하세요.';
			testStatus = 'idle';
			return;
		}
		saveError  = '';
		testStatus = 'loading';
		testReason = '';
		try {
			const result = await testConnectionRaw(buildConnectionTestPayload());
			testStatus = result.ok ? 'ok' : 'fail';
			testReason = result.reason ?? '';
		} catch (e) {
			testStatus = 'fail';
			testReason = e instanceof Error ? e.message : '알 수 없는 오류';
		}
	}

	// ── Delete ────────────────────────────────────────────────────────
	async function handleDelete() {
		if (!editServer) return;
		if (!deleteConfirm) {
			deleteConfirm = true;
			return;
		}
		if (!adminPassword.trim()) { saveError = '관리자 패스워드를 입력하세요.'; return; }
		deleting  = true;
		saveError = '';
		try {
			await deleteServer(editServer.id, adminPassword.trim());
			await onSaved();
			onClose();
		} catch (e) {
			saveError     = e instanceof Error ? e.message : '삭제 실패';
			deleteConfirm = false;
		} finally {
			deleting = false;
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onClose();
	}

	const externalFirewallIps = [
		'166.104.167.11',
		'166.104.168.168',
		'166.104.168.169',
		'166.104.168.170',
		'166.104.168.161'
	];
	const firewallRequestUrl =
		'https://portal.hanyang.ac.kr/port.do#!UDMyMTQ5MiRAXmh1YXMvJEBeJEBeTTMyMDI2NiRAXu2VmeuCtCDrsKntmZTrsr3si6Dssq0kQF5NMDAzNzk3JEBeMDMzNGU5ZWE4ZjdlYThiNGQxMjgxNjRmYWI4ZmE3OTE3YjJiNWUzMDdiYThkNjQ0OGRkOTgyZDU0ZTI3NzZiZQ==';
	const internalGuideSteps = [
		'평소 SSH 로그인에 쓰는 실제 계정을 그대로 입력합니다.',
		'그 계정으로 GPU / CPU / RAM / storage 조회 명령이 가능해야 합니다.',
		'비밀번호가 기본이지만 이미 키 로그인이 열려 있으면 SSH 키도 사용할 수 있습니다.'
	];
	const externalGuideSteps = [
		'정보통신처 방화벽 개방 요청을 먼저 넣습니다.',
		'연구실 네트워크에서 대상 서버로 실제 SSH 접속이 되는지 먼저 확인합니다.',
		'서버 정보와 함께, 서버의 authorized_keys 에 등록된 공개키와 짝이 되는 개인키를 입력합니다.',
		'연결 테스트를 통과하면 저장합니다.'
	];

	function sshUserHint() {
		return network === 'internal'
			? '평소 SSH 로그인에 쓰는 실제 계정명을 입력하세요. 예: ubuntu, ircv, root.'
			: '서버에 공개키를 등록한 동일한 계정명을 입력하세요.';
	}

	function authHint() {
		if (network === 'external') {
			return authMode === 'key'
				? '이 칸에는 공개키가 아니라, 서버에 등록한 공개키와 짝이 되는 개인키를 붙여넣습니다.'
				: '외부망은 보통 SSH 키 방식을 권장합니다. 비밀번호 방식은 서버 정책을 먼저 확인하세요.';
		}
		return authMode === 'key'
			? '내부망도 키 로그인 사용이 가능하면 개인키로 등록할 수 있습니다.'
			: '내부망은 보통 비밀번호 로그인 계정을 그대로 쓰면 됩니다.';
	}

	function hostPlaceholder() {
		return network === 'internal' ? '예: 166.104.167.11' : '예: 34.64.x.x 또는 public DNS';
	}

	function sshUserPlaceholder() {
		return network === 'internal' ? '예: ubuntu / ircv / root' : 'authorized_keys 등록 계정';
	}

	function guideTitle() {
		return network === 'internal'
			? '기존 SSH 계정으로 바로 등록'
			: '방화벽 개방과 연구실망 접속 확인이 먼저 필요';
	}

	function guideBody() {
		return network === 'internal'
			? internalGuideSteps
			: externalGuideSteps;
	}

	const inputCls =
		'bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm w-full focus:outline-none focus:border-white/30 text-white placeholder:text-white/25';
	const labelCls = 'text-xs text-white/50 mb-1 block';
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- Backdrop -->
{#if open}
	<div
		class="fixed inset-0 z-40 bg-black/50"
		role="button"
		tabindex="-1"
		aria-label="패널 닫기"
		onclick={onClose}
		onkeydown={(e) => { if (e.key === 'Enter') onClose(); }}
	></div>
{/if}

<!-- Slide-in panel -->
<aside
	class="fixed top-0 right-0 z-50 h-full w-full max-w-[480px] bg-surface border-l border-surface-border flex flex-col
		transition-transform duration-150 {open ? 'translate-x-0' : 'translate-x-full'}"
	aria-hidden={!open}
>
	<!-- Header -->
	<div class="flex items-center justify-between px-6 py-5 border-b border-surface-border shrink-0">
		<h2 class="text-sm font-semibold">
			{editServer ? '서버 편집' : '서버 등록'}
		</h2>
		<button
			class="btn-ghost w-7 h-7 flex items-center justify-center rounded-md p-0"
			onclick={onClose}
			aria-label="닫기"
		>
			<svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
				<line x1="18" y1="6" x2="6" y2="18"/>
				<line x1="6" y1="6" x2="18" y2="18"/>
			</svg>
		</button>
	</div>

	<!-- Scrollable body -->
	<div class="flex-1 overflow-y-auto px-6 py-5 flex flex-col gap-4">
		<!-- Server name -->
		<div>
			<label class={labelCls} for="sf-name">서버 이름</label>
			<input
				id="sf-name"
				type="text"
				placeholder="예: gpu-server-01"
				bind:value={name}
				class={inputCls}
			/>
		</div>

		<!-- Network -->
		<div>
			<div class={labelCls}>네트워크</div>
			<div class="grid grid-cols-2 gap-2">
				<label
					class={`rounded-2xl border px-3 py-3 transition-colors cursor-pointer ${
						network === 'internal'
							? 'border-white/18 bg-white/[0.09] text-white'
							: 'border-white/8 bg-white/[0.025] text-white/55 hover:text-white/72'
					}`}
				>
					<input type="radio" bind:group={network} value="internal" class="sr-only" />
					<span class="block text-sm font-medium">내부망</span>
					<span class="mt-0.5 block text-[11px] text-white/38">교내망 SSH</span>
				</label>
				<label
					class={`rounded-2xl border px-3 py-3 transition-colors cursor-pointer ${
						network === 'external'
							? 'border-white/18 bg-white/[0.09] text-white'
							: 'border-white/8 bg-white/[0.025] text-white/55 hover:text-white/72'
					}`}
				>
					<input type="radio" bind:group={network} value="external" class="sr-only" />
					<span class="block text-sm font-medium">외부망</span>
					<span class="mt-0.5 block text-[11px] text-white/38">public IP / DNS</span>
				</label>
			</div>
		</div>

		<div class="rounded-2xl border border-white/9 bg-white/[0.038] px-4 py-4">
			<div class="flex items-start justify-between gap-3">
				<div>
					<p class="text-[11px] font-medium uppercase tracking-[0.22em] text-white/32">Quick Guide</p>
					<p class="mt-1 text-sm font-medium text-white/88">{guideTitle()}</p>
				</div>
				<span class="rounded-full border border-white/8 px-2.5 py-1 text-[10px] uppercase tracking-[0.18em] text-white/36">
					{network === 'internal' ? 'internal' : 'external'}
				</span>
			</div>

			<div class="mt-3 grid gap-2">
				{#each guideBody() as step, index}
					<div class="flex gap-3 rounded-xl border border-white/7 bg-black/10 px-3 py-2.5">
						<span class="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/8 text-[10px] font-medium text-white/56">
							{index + 1}
						</span>
						<p class="text-[12px] leading-5 text-white/58">{step}</p>
					</div>
				{/each}
			</div>

			{#if network === 'external'}
				<div class="mt-3 rounded-xl border border-amber-300/14 bg-amber-300/[0.06] px-3 py-3">
					<p class="text-[11px] font-medium uppercase tracking-[0.18em] text-amber-200/72">Firewall Request</p>
					<p class="mt-1 text-[12px] leading-5 text-white/64">
						정보통신처 방화벽 개방 요청을 먼저 넣습니다.
					</p>
					<a
						href={firewallRequestUrl}
						target="_blank"
						rel="noreferrer"
						class="mt-2 inline-flex text-[12px] leading-5 text-amber-200/82 underline underline-offset-4 hover:text-amber-100"
					>
						학교 포털에서 신청하기
					</a>
					<p class="mt-2 text-[11px] leading-5 text-white/50">
						정책은 <span class="font-medium text-white/72">outbound</span> 로 신청하고, 아래 출발지 IP를 포함합니다.
					</p>
					<div class="mt-2 flex flex-wrap gap-1.5">
						{#each externalFirewallIps as ip}
							<span class="rounded-full border border-white/8 bg-black/14 px-2.5 py-1 font-mono text-[11px] text-white/52">
								{ip}
							</span>
						{/each}
					</div>
					<p class="mt-2 text-[11px] leading-5 text-white/48">
						폼 입력은 public key 자체가 아니라, 서버에 등록된 public key 와 짝이 되는 private key 입니다.
					</p>
				</div>
			{/if}
		</div>

		<!-- Host + Port -->
		<div class="flex gap-3">
			<div class="flex-1 min-w-0">
				<label class={labelCls} for="sf-host">호스트</label>
				<input
					id="sf-host"
					type="text"
					placeholder={hostPlaceholder()}
					bind:value={host}
					class={inputCls}
				/>
			</div>
			<div class="w-24 shrink-0">
				<label class={labelCls} for="sf-port">SSH 포트</label>
				<input
					id="sf-port"
					type="number"
					min="1"
					max="65535"
					bind:value={port}
					class={inputCls}
				/>
			</div>
		</div>

		<!-- SSH user -->
		<div>
			<label class={labelCls} for="sf-user">SSH 유저</label>
			<input
				id="sf-user"
				type="text"
				placeholder={sshUserPlaceholder()}
				bind:value={sshUser}
				class={inputCls}
			/>
			<p class="mt-1 text-[11px] leading-5 text-white/38">{sshUserHint()}</p>
		</div>

		<!-- Auth mode toggle -->
		<div>
			<div class={labelCls}>인증 방식</div>
			<div class="flex gap-4 mb-3">
				<label class="flex items-center gap-2 text-sm cursor-pointer">
					<input type="radio" bind:group={authMode} value="password" class="accent-white/60" />
					<span class="text-white/70">비밀번호</span>
				</label>
				<label class="flex items-center gap-2 text-sm cursor-pointer">
					<input type="radio" bind:group={authMode} value="key" class="accent-white/60" />
					<span class="text-white/70">SSH 키</span>
				</label>
			</div>

			{#if authMode === 'password'}
				<input
					type="password"
					placeholder={editServer ? '변경하려면 입력 (비워두면 유지)' : 'SSH 비밀번호'}
					bind:value={sshPassword}
					class={inputCls}
				/>
			{:else}
				<textarea
					placeholder={editServer
						? '변경하려면 붙여넣기 (비워두면 유지)'
						: 'SSH 개인키 붙여넣기 (-----BEGIN ...)'}
					bind:value={sshKey}
					rows="5"
					class="{inputCls} resize-none font-mono text-xs"
				></textarea>
			{/if}
			<p class="mt-1 text-[11px] leading-5 text-white/38">{authHint()}</p>
		</div>

		<!-- Divider -->
		<div class="border-t border-surface-border"></div>

		<!-- Admin password -->
		<div>
			<label class={labelCls} for="sf-admin-pw">관리자 패스워드</label>
			<input
				id="sf-admin-pw"
				type="password"
				placeholder="필수"
				bind:value={adminPassword}
				class={inputCls}
			/>
		</div>

		<!-- Connection test -->
		<div class="flex items-center gap-3">
			<button
				class="btn-ghost text-xs px-3 py-1.5 shrink-0"
				onclick={handleTest}
				disabled={testStatus === 'loading'}
			>
				{#if testStatus === 'loading'}
					<span class="inline-flex items-center gap-1.5">
						<span class="w-3 h-3 border border-white/30 border-t-white/70 rounded-full animate-spin"></span>
						테스트 중...
					</span>
				{:else}
					연결 테스트
				{/if}
			</button>

			{#if testStatus === 'ok'}
				<span class="text-xs text-emerald-400">연결 성공</span>
			{:else if testStatus === 'fail'}
				<span class="text-xs text-red-400">실패: {testReason || '알 수 없는 오류'}</span>
			{/if}
		</div>

		<!-- Inline error -->
		{#if saveError}
			<p class="text-xs text-red-400">{saveError}</p>
		{/if}
	</div>

	<!-- Footer -->
	<div class="shrink-0 border-t border-surface-border px-6 py-4 flex flex-col gap-3">

		<!-- Delete row (edit mode only) -->
		{#if editServer}
			<div class="flex items-center gap-3">
				<button
					class="text-xs px-3 py-1.5 rounded-lg border border-red-400/30 text-red-400 hover:bg-red-400/10 transition-colors disabled:opacity-40"
					onclick={handleDelete}
					disabled={deleting}
				>
					{#if deleting}
						<span class="inline-flex items-center gap-1.5">
							<span class="w-3 h-3 border border-red-400/30 border-t-red-400 rounded-full animate-spin"></span>
							삭제 중...
						</span>
					{:else if deleteConfirm}
						정말 삭제하시겠어요?
					{:else}
						서버 삭제
					{/if}
				</button>
				{#if deleteConfirm && !deleting}
					<button
						class="text-xs text-white/40 hover:text-white/70 transition-colors"
						onclick={() => { deleteConfirm = false; }}
					>
						취소
					</button>
				{/if}
			</div>
		{/if}

		<!-- Save / Cancel -->
		<div class="flex gap-2 justify-end">
			<button
				class="btn-ghost text-sm px-4 py-2"
				onclick={onClose}
				disabled={saving}
			>
				취소
			</button>
			<button
				class="text-sm px-4 py-2 bg-white/10 hover:bg-white/15 text-white rounded-lg transition-colors disabled:opacity-40"
				onclick={handleSave}
				disabled={saving}
			>
				{#if saving}
					<span class="inline-flex items-center gap-1.5">
						<span class="w-3 h-3 border border-white/30 border-t-white/70 rounded-full animate-spin"></span>
						저장 중...
					</span>
				{:else}
					저장
				{/if}
			</button>
		</div>
	</div>
</aside>
