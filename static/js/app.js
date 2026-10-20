/**
 * Python Sandbox Frontend Application
 * Handles Monaco Editor initialization, fallback editor, execution requests,
 * and responsive UI state transitions.
 */

(function () {
    'use strict';

    // Default editor template
    const DEFAULT_CODE = `print("Hello World")\n`;
    const MAX_CODE_BYTES = 32768;

    // DOM Elements
    const elements = {
        runBtn: document.getElementById('runBtn'),
        runBtnText: document.getElementById('runBtnText'),
        runSpinner: document.getElementById('runSpinner'),
        runIcon: document.getElementById('runIcon'),
        clearBtn: document.getElementById('clearBtn'),
        resetBtn: document.getElementById('resetBtn'),
        copyOutputBtn: document.getElementById('copyOutputBtn'),
        executionStatusBadge: document.getElementById('executionStatusBadge'),
        statusText: document.getElementById('statusText'),
        daemonStatusBadge: document.getElementById('daemonStatusBadge'),
        daemonPulse: document.getElementById('daemonPulse'),
        daemonLabel: document.getElementById('daemonLabel'),
        codeSizeCounter: document.getElementById('codeSizeCounter'),
        monacoContainer: document.getElementById('monacoContainer'),
        fallbackEditor: document.getElementById('fallbackEditor'),
        fallbackTextarea: document.getElementById('fallbackTextarea'),
        fallbackGutter: document.getElementById('fallbackGutter'),
        emptyState: document.getElementById('emptyState'),
        outputContent: document.getElementById('outputContent'),
        statusBanner: document.getElementById('statusBanner'),
        stdoutSection: document.getElementById('stdoutSection'),
        stdoutPre: document.getElementById('stdoutPre'),
        stderrSection: document.getElementById('stderrSection'),
        stderrPre: document.getElementById('stderrPre'),
        statTime: document.getElementById('statTime'),
        statExitCode: document.getElementById('statExitCode'),
        statTruncated: document.getElementById('statTruncated'),
        statExecId: document.getElementById('statExecId'),
    };

    let editorInstance = null;
    let isExecuting = false;

    /**
     * Measure UTF-8 byte length of a string
     */
    function getUtf8ByteLength(str) {
        return new TextEncoder().encode(str).length;
    }

    /**
     * Get current source code from Monaco or fallback textarea
     */
    function getCode() {
        if (editorInstance && typeof editorInstance.getValue === 'function') {
            return editorInstance.getValue();
        }
        return elements.fallbackTextarea.value;
    }

    /**
     * Set source code in Monaco or fallback textarea
     */
    function setCode(code) {
        if (editorInstance && typeof editorInstance.setValue === 'function') {
            editorInstance.setValue(code);
        } else {
            elements.fallbackTextarea.value = code;
            updateFallbackGutter();
        }
        updateCodeSizeCounter();
    }

    /**
     * Update the code size indicator
     */
    function updateCodeSizeCounter() {
        const code = getCode();
        const bytes = getUtf8ByteLength(code);
        elements.codeSizeCounter.textContent = `${bytes.toLocaleString()} / ${MAX_CODE_BYTES.toLocaleString()} bytes`;
        if (bytes > MAX_CODE_BYTES) {
            elements.codeSizeCounter.style.color = 'var(--color-rose)';
        } else {
            elements.codeSizeCounter.style.color = 'var(--text-muted)';
        }
    }

    /**
     * Update the gutter line numbers in fallback editor
     */
    function updateFallbackGutter() {
        const lines = elements.fallbackTextarea.value.split('\n').length;
        let gutterText = '';
        for (let i = 1; i <= lines; i++) {
            gutterText += i + '\n';
        }
        elements.fallbackGutter.innerText = gutterText;
    }

    /**
     * Set Execution Status Badge
     * States: IDLE | RUNNING | SUCCESS | ERROR | TIMEOUT | SECURITY ERROR
     */
    function setStatus(state, customText) {
        elements.executionStatusBadge.className = 'status-indicator';
        
        switch (state) {
            case 'RUNNING':
                elements.executionStatusBadge.classList.add('status-running');
                elements.statusText.textContent = customText || 'RUNNING';
                break;
            case 'SUCCESS':
                elements.executionStatusBadge.classList.add('status-success');
                elements.statusText.textContent = customText || 'SUCCESS';
                break;
            case 'ERROR':
                elements.executionStatusBadge.classList.add('status-error');
                elements.statusText.textContent = customText || 'ERROR';
                break;
            case 'TIMEOUT':
                elements.executionStatusBadge.classList.add('status-timeout');
                elements.statusText.textContent = customText || 'TIMEOUT';
                break;
            case 'SECURITY ERROR':
                elements.executionStatusBadge.classList.add('status-security');
                elements.statusText.textContent = customText || 'SECURITY ERROR';
                break;
            case 'IDLE':
            default:
                elements.executionStatusBadge.classList.add('status-idle');
                elements.statusText.textContent = 'IDLE';
                break;
        }
    }

    /**
     * Set Execution Controls Busy/Idle state
     */
    function setRunningState(running) {
        isExecuting = running;
        elements.runBtn.disabled = running;
        if (running) {
            elements.runSpinner.classList.remove('hidden');
            elements.runIcon.classList.add('hidden');
            elements.runBtnText.textContent = 'Executing...';
            setStatus('RUNNING');
        } else {
            elements.runSpinner.classList.add('hidden');
            elements.runIcon.classList.remove('hidden');
            elements.runBtnText.textContent = 'Run Code';
        }
    }

    /**
     * Initialize Monaco Editor with Fallback mechanism
     */
    function initEditor() {
        // Check if Monaco loader is available
        if (typeof window.require !== 'undefined') {
            try {
                window.require.config({
                    paths: {
                        vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs',
                    },
                });

                window.require(['vs/editor/editor.main'], function () {
                    try {
                        editorInstance = monaco.editor.create(elements.monacoContainer, {
                            value: DEFAULT_CODE,
                            language: 'python',
                            theme: 'vs-dark',
                            automaticLayout: true,
                            fontSize: 14,
                            fontFamily: "'Fira Code', 'JetBrains Mono', Consolas, monospace",
                            fontLigatures: true,
                            minimap: { enabled: false },
                            scrollBeyondLastLine: false,
                            renderLineHighlight: 'all',
                            cursorBlinking: 'smooth',
                            padding: { top: 12, bottom: 12 },
                            tabSize: 4,
                            insertSpaces: true,
                        });

                        editorInstance.onDidChangeModelContent(updateCodeSizeCounter);

                        // Keyboard shortcut: Ctrl+Enter or Cmd+Enter to run
                        editorInstance.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, function () {
                            if (!isExecuting) executeCode();
                        });

                        updateCodeSizeCounter();
                    } catch (e) {
                        console.warn('Monaco initialization failed, falling back to textarea:', e);
                        enableFallbackEditor();
                    }
                }, function (err) {
                    console.warn('Monaco script load failed, falling back to textarea:', err);
                    enableFallbackEditor();
                });
                return;
            } catch (e) {
                console.warn('Monaco configuration failed:', e);
            }
        }

        enableFallbackEditor();
    }

    /**
     * Activate simple textarea editor fallback
     */
    function enableFallbackEditor() {
        elements.monacoContainer.classList.add('hidden');
        elements.fallbackEditor.classList.remove('hidden');
        elements.fallbackTextarea.value = DEFAULT_CODE;
        updateFallbackGutter();
        updateCodeSizeCounter();

        elements.fallbackTextarea.addEventListener('input', () => {
            updateFallbackGutter();
            updateCodeSizeCounter();
        });

        elements.fallbackTextarea.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                if (!isExecuting) executeCode();
            }
            // Tab key support
            if (e.key === 'Tab') {
                e.preventDefault();
                const start = elements.fallbackTextarea.selectionStart;
                const end = elements.fallbackTextarea.selectionEnd;
                elements.fallbackTextarea.value = elements.fallbackTextarea.value.substring(0, start) + '    ' + elements.fallbackTextarea.value.substring(end);
                elements.fallbackTextarea.selectionStart = elements.fallbackTextarea.selectionEnd = start + 4;
                updateFallbackGutter();
            }
        });
    }

    /**
     * Poll Docker Health Check
     */
    async function checkHealth() {
        try {
            const resp = await fetch('/api/health');
            const data = await resp.json();
            if (resp.ok && data.status === 'healthy') {
                elements.daemonPulse.className = 'pulse-dot online';
                elements.daemonLabel.textContent = `Docker: Healthy (${data.runtime || 'runc'})`;
            } else {
                elements.daemonPulse.className = 'pulse-dot offline';
                elements.daemonLabel.textContent = `Docker: ${data.reason || 'Degraded'}`;
            }
        } catch (e) {
            elements.daemonPulse.className = 'pulse-dot offline';
            elements.daemonLabel.textContent = 'Docker: Offline';
        }
    }

    /**
     * Execute Code via POST /api/execute
     */
    async function executeCode() {
        if (isExecuting) return;

        const code = getCode();
        if (!code.trim()) {
            setStatus('ERROR', 'EMPTY CODE');
            showBanner('Source code cannot be empty.', 'banner-error');
            return;
        }

        setRunningState(true);
        clearOutput(false);

        try {
            const response = await fetch('/api/execute', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ code: code }),
            });

            const data = await response.json();
            handleExecutionResponse(response.status, data);

        } catch (err) {
            console.error('Execution request error:', err);
            setStatus('ERROR', 'NETWORK ERROR');
            showBanner(`Network error: ${err.message}. Please check if the sandbox server is reachable.`, 'banner-error');
        } finally {
            setRunningState(false);
        }
    }

    /**
     * Render API Execution Response
     */
    function handleExecutionResponse(statusCode, data) {
        elements.emptyState.classList.add('hidden');
        elements.outputContent.classList.remove('hidden');

        // Status code mappings
        if (statusCode === 200) {
            if (data.status === 'timeout') {
                setStatus('TIMEOUT');
                showBanner(`⏱ ${data.error || 'Execution timed out.'}`, 'banner-timeout');
            } else if (data.exit_code === 0) {
                setStatus('SUCCESS');
                hideBanner();
            } else {
                setStatus('ERROR', `EXIT ${data.exit_code}`);
                hideBanner();
            }

            // Render stdout
            if (data.stdout && data.stdout.trim().length > 0) {
                elements.stdoutPre.textContent = data.stdout;
                elements.stdoutSection.classList.remove('hidden');
            } else {
                elements.stdoutPre.textContent = '(No standard output)';
                elements.stdoutSection.classList.remove('hidden');
            }

            // Render stderr
            if (data.stderr && data.stderr.trim().length > 0) {
                elements.stderrPre.textContent = data.stderr;
                elements.stderrSection.classList.remove('hidden');
            } else {
                elements.stderrSection.classList.add('hidden');
            }

            // Update stats bar
            elements.statTime.textContent = data.execution_time_ms !== undefined ? `${(data.execution_time_ms / 1000).toFixed(2)}s` : '—';
            elements.statExitCode.textContent = data.exit_code !== undefined ? data.exit_code : '—';
            elements.statTruncated.textContent = data.truncated ? 'Yes' : 'No';
            elements.statExecId.textContent = data.execution_id || '—';
            elements.statExecId.title = data.execution_id || '';

        } else if (statusCode === 400) {
            setStatus('SECURITY ERROR');
            showBanner(`⚠️ Validation Error: ${data.error}`, 'banner-security');
            elements.stdoutSection.classList.add('hidden');
            elements.stderrSection.classList.add('hidden');
            resetStats();

        } else if (statusCode === 429) {
            setStatus('SECURITY ERROR', 'RATE LIMITED');
            showBanner(`⏳ Rate Limit Exceeded: ${data.error}`, 'banner-timeout');
            resetStats();

        } else if (statusCode === 503) {
            setStatus('ERROR', 'AT CAPACITY');
            showBanner(`🚦 Sandbox at capacity: ${data.error}`, 'banner-timeout');
            resetStats();

        } else {
            setStatus('ERROR', 'SERVER ERROR');
            showBanner(`❌ Internal Error: ${data.error || 'Execution could not be completed.'}`, 'banner-error');
            resetStats();
        }
    }

    function showBanner(message, typeClass) {
        elements.statusBanner.className = `status-banner ${typeClass}`;
        elements.statusBanner.textContent = message;
        elements.statusBanner.classList.remove('hidden');
    }

    function hideBanner() {
        elements.statusBanner.classList.add('hidden');
    }

    function resetStats() {
        elements.statTime.textContent = '—';
        elements.statExitCode.textContent = '—';
        elements.statTruncated.textContent = '—';
        elements.statExecId.textContent = '—';
    }

    function clearOutput(resetStatusToIdle = true) {
        elements.stdoutPre.textContent = '';
        elements.stderrPre.textContent = '';
        elements.stdoutSection.classList.add('hidden');
        elements.stderrSection.classList.add('hidden');
        hideBanner();
        elements.outputContent.classList.add('hidden');
        elements.emptyState.classList.remove('hidden');
        resetStats();
        if (resetStatusToIdle) setStatus('IDLE');
    }

    function copyOutput() {
        let text = '';
        if (!elements.stdoutSection.classList.contains('hidden')) {
            text += '--- STDOUT ---\n' + elements.stdoutPre.textContent + '\n';
        }
        if (!elements.stderrSection.classList.contains('hidden')) {
            text += '--- STDERR ---\n' + elements.stderrPre.textContent + '\n';
        }
        if (!text && !elements.statusBanner.classList.contains('hidden')) {
            text = elements.statusBanner.textContent;
        }

        if (text) {
            navigator.clipboard.writeText(text).then(() => {
                const originalText = elements.copyOutputBtn.innerHTML;
                elements.copyOutputBtn.innerHTML = '<span>Copied!</span>';
                setTimeout(() => {
                    elements.copyOutputBtn.innerHTML = originalText;
                }, 1800);
            });
        }
    }

    // Event Listeners
    elements.runBtn.addEventListener('click', executeCode);
    elements.clearBtn.addEventListener('click', () => clearOutput(true));
    elements.resetBtn.addEventListener('click', () => {
        setCode(DEFAULT_CODE);
        clearOutput(true);
    });
    elements.copyOutputBtn.addEventListener('click', copyOutput);

    // Initialize application
    initEditor();
    checkHealth();

    // Periodic health check every 30 seconds
    setInterval(checkHealth, 30000);

})();
