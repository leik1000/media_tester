const { createApp, ref, reactive, watch, onMounted, computed } = Vue;

const VIDEO_MODEL_CAPS = {
    'kling-video-3.0': {
        ratios: ['1:1', '16:9', '9:16'],
        resolutions: ['720p', '1080p'],
        durationRange: [3, 15],
        maxRefs: 1,
        supportsStartEnd: false,
        supportsVideoReference: false,
    },
    'kling-video-o3-omni': {
        ratios: ['1:1', '16:9', '9:16'],
        resolutions: ['720p', '1080p'],
        durationRange: [3, 15],
        maxRefs: 7,
        supportsStartEnd: true,
        supportsVideoReference: true,
    },
    'sora2': {
        ratios: ['16:9', '9:16'],
        resolutions: ['720p'],
        durations: [4, 8, 12],
        maxRefs: 1,
        supportsStartEnd: false,
        supportsVideoReference: false,
    },
    'sora-v3-pro': {
        ratios: ['21:9', '1:1', '4:3', '3:4', '16:9', '9:16'],
        resolutions: ['480p', '720p'],
        durationRange: [4, 15],
        maxRefs: 4,
        supportsStartEnd: true,
        supportsVideoReference: true,
    },
    'sora-v3-fast': {
        ratios: ['21:9', '1:1', '4:3', '3:4', '16:9', '9:16'],
        resolutions: ['480p', '720p'],
        durationRange: [4, 15],
        maxRefs: 4,
        supportsStartEnd: true,
        supportsVideoReference: true,
    },
    'veo31-fast': {
        ratios: ['16:9', '9:16'],
        resolutions: ['720p', '1080p'],
        durations: [4, 6, 8],
        maxRefs: 1,
        supportsStartEnd: false,
        supportsVideoReference: false,
    },
};

const app = createApp({
    setup() {
        const tab = ref('image');
        const isSubmitting = ref(false);
        const currentLogs = ref([]);
        const currentResult = ref(null);
        const results = ref([]);
        const selectedResult = ref(null);
        const showLogs = ref(false);
        const configLoaded = ref(false);
        const currentPage = ref(1);
        const pageSize = 25;
        let saveConfigTimer = null;
        const imageFiles = ref([]);
        const videoFiles = ref([]);

        const isLoading = computed(() =>
            results.value.some(item => item.status === 'starting' || item.status === 'running')
        );
        const totalPages = computed(() => Math.max(1, Math.ceil(results.value.length / pageSize)));
        const paginatedResults = computed(() => {
            const start = (currentPage.value - 1) * pageSize;
            return results.value.slice(start, start + pageSize);
        });

        const config = reactive({
            baseUrl: 'https://api.pixellelabs.com',
            enableProxy: true,
            proxyUrl: 'http://127.0.0.1:10808',
            defaultApiKey: '',
            gptImage2ApiKey: '',
            gemini3ProImageApiKey: '',
            gemini31FlashImageApiKey: '',
            videoApiKey: ''
        });

        const image = reactive({
            model: 'gemini-3-pro-image-preview',
            prompt: '电影感山间日出，云雾缓慢流动',
            aspectRatio: '16:9',
            size: '2K',
            quality: 'medium',
            imageUrls: ''
        });

        const video = reactive({
            model: 'sora2',
            prompt: '电影感蜂鸟飞过阳光花园',
            aspectRatio: '16:9',
            resolution: '720p',
            duration: 4,
            createPath: '/v1/videos',
            statusPath: '/v1/videos/{task_id}',
            startFrame: '',
            endFrame: '',
            videoReference: '',
            imageUrls: ''
        });

        const videoModelOptions = Object.keys(VIDEO_MODEL_CAPS);
        const currentVideoCapability = computed(() => VIDEO_MODEL_CAPS[video.model] || VIDEO_MODEL_CAPS.sora2);
        const videoDurationOptions = computed(() => currentVideoCapability.value.durations || []);
        const videoDurationRange = computed(() => currentVideoCapability.value.durationRange || null);
        const videoDurationMin = computed(() => videoDurationRange.value ? videoDurationRange.value[0] : null);
        const videoDurationMax = computed(() => videoDurationRange.value ? videoDurationRange.value[1] : null);

        const normalizeVideoSettings = () => {
            if (!VIDEO_MODEL_CAPS[video.model]) video.model = 'sora2';
            const cap = currentVideoCapability.value;
            if (!cap.ratios.includes(video.aspectRatio)) video.aspectRatio = cap.ratios[0];
            if (!cap.resolutions.includes(video.resolution)) video.resolution = cap.resolutions[0];
            if (cap.durations) {
                const value = Number(video.duration);
                if (!cap.durations.includes(value)) video.duration = cap.durations[0];
            } else if (cap.durationRange) {
                const [min, max] = cap.durationRange;
                const value = Number(video.duration) || min;
                video.duration = Math.min(max, Math.max(min, value));
            }
            if (!cap.supportsStartEnd) {
                video.startFrame = '';
                video.endFrame = '';
            }
            if (!cap.supportsVideoReference) {
                video.videoReference = '';
            }
        };

        onMounted(async () => {
            await loadSavedConfig();
            configLoaded.value = true;
            await loadPersistedAssets();
        });

        const applySavedConfig = (savedData) => {
            Object.assign(config, savedData.config || {});
            if (!config.defaultApiKey && savedData.config?.apiKey) {
                config.defaultApiKey = savedData.config.apiKey;
            }
            Object.assign(image, savedData.image || {});
            Object.assign(video, savedData.video || {});
            normalizeVideoSettings();
        };

        const loadSavedConfig = async () => {
            try {
                const res = await fetch('/api/config');
                const data = await res.json();
                if (res.ok && data.config && Object.keys(data.config).length) {
                    applySavedConfig(data.config);
                }
            } catch (e) {
                console.error('读取数据库配置失败', e);
            }
        };


        const loadPersistedAssets = async () => {
            try {
                const res = await fetch('/api/assets');
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || '加载历史资产失败');
                results.value = (data.assets || []).map(item => ({
                    logs: [`[系统] 已从 downloads/${item.filename} 加载。`],
                    ...item,
                }));
                currentPage.value = 1;
                if (results.value.length) {
                    currentResult.value = results.value[0];
                    currentLogs.value = currentResult.value.logs || [];
                }
            } catch (e) {
                console.error('加载历史资产失败', e);
            }
        };

        watch([config, image, video], () => {
            if (!configLoaded.value) return;
            const payload = JSON.stringify({ config, image, video });
            clearTimeout(saveConfigTimer);
            saveConfigTimer = setTimeout(() => {
                fetch('/api/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: payload,
                }).catch(e => console.error('保存数据库配置失败', e));
            }, 400);
        }, { deep: true });

        watch(() => video.model, normalizeVideoSettings);

        const scrollLogs = () => {
            const logContainer = document.querySelector('.overflow-auto.font-mono');
            if (logContainer) {
                setTimeout(() => {
                    logContainer.scrollTop = logContainer.scrollHeight;
                }, 10);
            }
        };

        const pushLog = (msg) => {
            currentLogs.value.push(msg);
            scrollLogs();
        };

        const submitTask = async () => {
            if (isSubmitting.value) return;
            isSubmitting.value = true;

            if (tab.value === 'image') {
                await runImageTask();
            } else {
                await runVideoTask();
            }
        };

        const resolveImageApiKey = (model) => {
            const keyByModel = {
                'gpt-image-2': config.gptImage2ApiKey,
                'gemini-3-pro-image-preview': config.gemini3ProImageApiKey,
                'gemini-3.1-flash-image-preview': config.gemini31FlashImageApiKey,
            };
            return keyByModel[model] || config.defaultApiKey || '';
        };

        const resolveVideoApiKey = () => config.videoApiKey || config.defaultApiKey || '';

        const appendCommonImageFields = (formData, source, apiKey, options = {}) => {
            formData.append('base_url', config.baseUrl);
            formData.append('proxy_url', config.enableProxy ? (config.proxyUrl || '') : '');
            formData.append('api_key', apiKey || '');
            formData.append('model', source.model);
            formData.append('prompt', source.prompt);
            formData.append('aspect_ratio', source.aspectRatio);
            if (options.includeReferences !== false) {
                source.imageUrls.split('\n').map(s => s.trim()).filter(Boolean).forEach(url => {
                    formData.append('image_url', url);
                });
            }
        };

        const onImageFilesChange = (event) => {
            imageFiles.value = Array.from(event.target.files || []);
        };

        const onVideoFilesChange = (event) => {
            videoFiles.value = Array.from(event.target.files || []);
        };

        const appendReferenceUrl = (target, url) => {
            const value = String(url || '').trim();
            if (!value) return;
            const current = target.imageUrls
                .split('\n')
                .map(item => item.trim())
                .filter(Boolean);
            if (!current.includes(value)) {
                current.push(value);
            }
            target.imageUrls = current.join('\n');
        };

        const onResultDragStart = (event, item) => {
            if (item.status !== 'completed' || !item.url) return;
            event.dataTransfer.effectAllowed = 'copy';
            event.dataTransfer.setData('text/plain', item.url);
            event.dataTransfer.setData('application/x-media-tester-url', item.url);
        };

        const onReferenceDrop = (event, targetName) => {
            const url = event.dataTransfer.getData('application/x-media-tester-url') || event.dataTransfer.getData('text/plain');
            appendReferenceUrl(targetName === 'video' ? video : image, url);
            pushLog(`[输入] 已从画廊添加参考图：${url}`);
        };

        const createPlaceholder = (item) => {
            const result = {
                id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
                taskId: null,
                status: 'starting',
                url: null,
                error: null,
                logs: [],
                createdAt: new Date().toLocaleString(),
                ...item,
            };
            results.value.unshift(result);
            currentPage.value = 1;
            currentResult.value = result;
            currentLogs.value = result.logs;
            return result;
        };

        const updateResult = (id, patch) => {
            const item = results.value.find(result => result.id === id);
            if (!item) return null;
            Object.assign(item, patch);
            if (currentResult.value?.id === id) {
                currentResult.value = item;
                currentLogs.value = item.logs || [];
                scrollLogs();
            }
            return item;
        };

        const openPreview = (item) => {
            currentResult.value = item;
            currentLogs.value = item.logs || [];
            if ((item.status === 'completed' && item.url) || item.status === 'error' || item.status === 'failed') {
                selectedResult.value = item;
            }
            scrollLogs();
        };

        const formatJson = (value) => {
            if (!value) return '';
            try {
                return JSON.stringify(value, null, 2);
            } catch (_) {
                return String(value);
            }
        };

        const closePreview = () => {
            selectedResult.value = null;
        };

        const toggleLogs = () => {
            showLogs.value = !showLogs.value;
            if (showLogs.value) scrollLogs();
        };

        const closeLogs = () => {
            showLogs.value = false;
        };

        const clearResults = () => {
            results.value = [];
            currentResult.value = null;
            selectedResult.value = null;
            currentLogs.value = [];
            pushLog(`[系统] 已清空当前画廊显示。`);
        };

        const nextPage = () => {
            currentPage.value = Math.min(totalPages.value, currentPage.value + 1);
        };

        const prevPage = () => {
            currentPage.value = Math.max(1, currentPage.value - 1);
        };

        const runImageTask = async () => {
            const meta = image.model === 'gpt-image-2'
                ? `${image.size} · ${image.aspectRatio} · ${image.quality || 'medium'}`
                : `${image.size} · ${image.aspectRatio}`;
            const placeholder = createPlaceholder({
                type: 'image',
                model: image.model,
                prompt: image.prompt,
                meta,
                logs: [
                    `[系统] 图片任务已加入队列。`,
                    `[配置] 模型：${image.model} | ${meta}`,
                ],
            });
            if (imageFiles.value.length) {
                placeholder.logs.push(`[输入] 已附加 ${imageFiles.value.length} 张本地参考图。`);
            }
            currentLogs.value = placeholder.logs;

            try {
                if (!usingStartEnd) {
                    const urlRefCount = video.imageUrls.split('\n').map(s => s.trim()).filter(Boolean).length;
                    const referenceCount = urlRefCount + videoFiles.value.length;
                    if (referenceCount > cap.maxRefs) {
                        throw new Error(`当前模型最多支持 ${cap.maxRefs} 张参考图。`);
                    }
                }
                const payload = new FormData();
                appendCommonImageFields(payload, image, resolveImageApiKey(image.model));
                payload.append('image_size', image.size);
                if (image.model === 'gpt-image-2') {
                    payload.append('quality', image.quality || 'medium');
                }
                imageFiles.value.forEach(file => payload.append('image_file', file));

                const res = await fetch('/api/image', { method: 'POST', body: payload });
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || '图片任务启动失败');

                updateResult(placeholder.id, {
                    taskId: data.internal_task_id,
                    status: 'running',
                    logs: [...placeholder.logs, `[系统] 后端已接收任务：${data.internal_task_id}。`],
                });
                pollTask(data.internal_task_id, placeholder.id);
            } catch (err) {
                updateResult(placeholder.id, {
                    status: 'error',
                    error: err.message,
                    logs: [...placeholder.logs, `[错误] ${err.message}`],
                });
                console.error(err);
            } finally {
                isSubmitting.value = false;
            }
        };

        const runVideoTask = async () => {
            normalizeVideoSettings();
            const cap = currentVideoCapability.value;
            const usingStartEnd = cap.supportsStartEnd && (String(video.startFrame || '').trim() || String(video.endFrame || '').trim());
            const meta = `${video.duration}s · ${video.aspectRatio} · ${video.resolution}`;
            const placeholder = createPlaceholder({
                type: 'video',
                model: video.model,
                prompt: video.prompt,
                meta,
                logs: [
                    `[系统] 视频任务已加入队列。`,
                    `[配置] 模型：${video.model} | ${meta}`,
                ],
            });
            if (videoFiles.value.length) {
                placeholder.logs.push(`[输入] 已附加 ${videoFiles.value.length} 张本地参考图。`);
            }
            currentLogs.value = placeholder.logs;

            try {
                const payload = new FormData();
                appendCommonImageFields(payload, video, resolveVideoApiKey(), { includeReferences: !usingStartEnd });
                payload.append('duration', video.duration);
                payload.append('resolution', video.resolution);
                payload.append('create_path', video.createPath);
                payload.append('status_path', video.statusPath);
                if (cap.supportsStartEnd) {
                    if (video.startFrame.trim()) payload.append('start_frame', video.startFrame.trim());
                    if (video.endFrame.trim()) payload.append('end_frame', video.endFrame.trim());
                }
                if (cap.supportsVideoReference && video.videoReference.trim()) {
                    payload.append('video_reference', video.videoReference.trim());
                }
                if (!usingStartEnd) {
                    videoFiles.value.forEach(file => payload.append('image_file', file));
                }

                const res = await fetch('/api/video', { method: 'POST', body: payload });
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || '视频任务启动失败');

                updateResult(placeholder.id, {
                    taskId: data.internal_task_id,
                    status: 'running',
                    logs: [...placeholder.logs, `[系统] 后端已接收任务：${data.internal_task_id}。`],
                });
                pollTask(data.internal_task_id, placeholder.id);
            } catch (err) {
                updateResult(placeholder.id, {
                    status: 'error',
                    error: err.message,
                    logs: [...placeholder.logs, `[错误] ${err.message}`],
                });
                console.error(err);
            } finally {
                isSubmitting.value = false;
            }
        };

        const pollTask = (taskId, resultId) => {
            const pollInterval = setInterval(async () => {
                try {
                    const res = await fetch(`/api/task/${taskId}`);
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.message || '任务状态查询失败');

                    const item = results.value.find(result => result.id === resultId);
                    if (!item) {
                        clearInterval(pollInterval);
                        return;
                    }

                    const logs = data.logs?.length ? data.logs : item.logs;
                    const patch = {
                        status: data.status || item.status,
                        logs,
                        error: data.error || item.error,
                        apiTaskId: data.api_task_id || item.apiTaskId,
                        requestPayload: data.request_payload || item.requestPayload,
                        raw: data.raw || item.raw,
                    };

                    if (data.status === 'completed') {
                        patch.url = data.asset?.url || (item.type === 'image' ? data.image_url : data.media_url);
                        patch.filename = data.asset?.filename || item.filename;
                        patch.remote_url = data.asset?.remote_url || data.remote_url || item.remote_url;
                        patch.createdAt = data.asset?.createdAt || item.createdAt;
                    }

                    updateResult(resultId, patch);

                    if (data.status === 'completed' || data.status === 'failed' || data.status === 'error') {
                        clearInterval(pollInterval);
                        const finalItem = results.value.find(result => result.id === resultId);
                        if (finalItem && finalItem.status === 'completed') {
                            finalItem.logs = [...(finalItem.logs || []), `[系统] ${finalItem.type === 'image' ? '图片' : '视频'}已完成。`];
                        }
                        if (finalItem && finalItem.status !== 'completed' && !finalItem.error) {
                            finalItem.error = '任务失败。';
                        }
                        if (currentResult.value?.id === resultId) {
                            currentLogs.value = finalItem?.logs || [];
                            scrollLogs();
                        }
                    }
                } catch (e) {
                    console.error('轮询失败', e);
                }
            }, 2000);
        };

        return {
            tab, isLoading, isSubmitting,
            config, image, video,
            videoModelOptions, currentVideoCapability, videoDurationOptions, videoDurationRange, videoDurationMin, videoDurationMax,
            imageFiles, videoFiles,
            onImageFilesChange, onVideoFilesChange,
            onResultDragStart, onReferenceDrop,
            results, paginatedResults, currentPage, totalPages, selectedResult, showLogs,
            openPreview, closePreview, toggleLogs, closeLogs, clearResults,
            nextPage, prevPage,
            submitTask,
            currentLogs, currentResult, formatJson
        };
    }
});

app.mount('#app');
