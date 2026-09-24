const { createApp, ref, reactive, watch, onMounted, onBeforeUnmount, computed } = Vue;

const rawFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
    const res = await rawFetch(...args);
    if (res.status === 401 && window.location.pathname !== '/login') {
        window.location.href = '/login';
    }
    return res;
};

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
    'gemini-omni-flash': {
        ratios: ['16:9', '9:16'],
        resolutions: [],
        durations: [4, 6, 8, 10],
        maxRefs: 5,
        supportsStartEnd: false,
        supportsVideoReference: true,
        videoReferenceField: 'reference_video',
        exposesResolution: false,
    },
    'sora-v3-pro': {
        ratios: ['21:9', '1:1', '4:3', '3:4', '16:9', '9:16'],
        resolutions: ['720p'],
        durationRange: [4, 15],
        maxRefs: 9,
        maxVideoRefs: 3,
        maxAudioRefs: 3,
        maxTotalRefs: 12,
        supportsStartEnd: false,
        supportsVideoReference: true,
        supportsAudioReference: true,
        usesReferenceUrlArrays: true,
    },
    'sora-v3-fast': {
        ratios: ['21:9', '1:1', '4:3', '3:4', '16:9', '9:16'],
        resolutions: ['720p'],
        durationRange: [4, 15],
        maxRefs: 9,
        maxVideoRefs: 3,
        maxAudioRefs: 3,
        maxTotalRefs: 12,
        supportsStartEnd: false,
        supportsVideoReference: true,
        supportsAudioReference: true,
        usesReferenceUrlArrays: true,
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

const IMAGE_SIZE_OPTIONS = ['1K', '2K', '4K'];
const BASE_IMAGE_RATIOS = ['1:1', '4:3', '3:4', '5:4', '4:5', '3:2', '2:3', '16:9', '9:16', '21:9'];
const SEEDREAM_IMAGE_RATIOS = ['9:21', '9:16', '2:3', '3:4', '1:1', '4:3', '3:2', '16:9', '21:9'];
const GEMINI_LITE_IMAGE_RATIOS = ['8:1', '4:1', '21:9', '16:9', '3:2', '4:3', '5:4', '1:1'];
const IMAGE_MODEL_CAPS = {
    'gemini-3-pro-image-preview': {
        ratios: ['auto', ...BASE_IMAGE_RATIOS],
        sizes: IMAGE_SIZE_OPTIONS,
    },
    'gemini-3.1-flash-image-preview': {
        ratios: ['auto', ...BASE_IMAGE_RATIOS, '1:4', '4:1', '1:8', '8:1'],
        sizes: IMAGE_SIZE_OPTIONS,
    },
    'gemini-3.1-flash-lite-image': {
        ratios: GEMINI_LITE_IMAGE_RATIOS,
        sizes: ['1K'],
    },
    'gpt-image-2': {
        ratios: BASE_IMAGE_RATIOS,
        sizes: IMAGE_SIZE_OPTIONS,
        supportsQuality: true,
    },
    'gpt-image-2.5-flare': {
        ratios: BASE_IMAGE_RATIOS,
        sizes: IMAGE_SIZE_OPTIONS,
        supportsQuality: true,
    },
    'gpt-image-2.5-sunburst': {
        ratios: BASE_IMAGE_RATIOS,
        sizes: IMAGE_SIZE_OPTIONS,
        supportsQuality: true,
    },
    'seedream-5-pro': {
        ratios: SEEDREAM_IMAGE_RATIOS,
        sizes: ['1K', '2K'],
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
        const previewImageScale = ref(1);
        const previewImagePan = reactive({ x: 0, y: 0 });
        const previewImageDrag = reactive({
            active: false,
            pointerId: null,
            startX: 0,
            startY: 0,
            startPanX: 0,
            startPanY: 0,
        });
        const previewImageTransformOrigin = ref('center center');
        const showLogs = ref(false);
        const showSystemConfig = ref(false);
        const configLoaded = ref(false);
        const currentPage = ref(1);
        const totalItems = ref(0);
        const totalPages = ref(1);
        const galleryFilter = ref('all');
        const pageSize = 20;
        let saveConfigTimer = null;
        const activePolls = new Map();
        const videoReferenceFiles = ref([]);
        const audioReferenceFiles = ref([]);
        const referenceImages = reactive({ image: [], video: [] });
        const referenceControllers = new Map();

        const isLoading = computed(() =>
            results.value.some(item => item.status === 'starting' || item.status === 'running')
        );
        const paginatedResults = computed(() => results.value);

        const countReferenceUrls = (value) => String(value || '')
            .split('\n')
            .map(item => item.trim())
            .filter(Boolean)
            .length;
        const imageReferenceCount = computed(() => referenceImages.image.length);
        const videoReferenceCount = computed(() => referenceImages.video.length);
        const videoUrlReferenceCount = computed(() => countReferenceUrls(video.videoUrls) + videoReferenceFiles.value.length);
        const audioUrlReferenceCount = computed(() => countReferenceUrls(video.audioUrls) + audioReferenceFiles.value.length);
        const totalVideoMediaReferenceCount = computed(() => (
            videoReferenceCount.value + videoUrlReferenceCount.value + audioUrlReferenceCount.value
        ));
        const previewImageStyle = computed(() => ({
            transform: `translate3d(${previewImagePan.x}px, ${previewImagePan.y}px, 0) scale(${previewImageScale.value})`,
            transformOrigin: previewImageTransformOrigin.value,
            cursor: previewImageScale.value > 1 ? (previewImageDrag.active ? 'grabbing' : 'grab') : 'zoom-in',
            transition: previewImageDrag.active ? 'none' : 'transform 100ms ease-out',
            userSelect: 'none',
        }));

        const config = reactive({
            baseUrl: 'https://api.pixellelabs.com',
            publicMediaBaseUrl: '',
            enableProxy: true,
            proxyUrl: 'http://127.0.0.1:10808',
            gptImage2ApiKey: '',
            gptImage25FlareApiKey: '',
            gptImage25SunburstApiKey: '',
            seedreamImageApiKey: '',
            gemini3ProImageApiKey: '',
            gemini31FlashImageApiKey: '',
            gemini31FlashLiteImageApiKey: '',
            videoApiKeys: Object.fromEntries(Object.keys(VIDEO_MODEL_CAPS).map(model => [model, '']))
        });

        const authSettings = reactive({
            username: 'admin',
            newPassword: '',
            message: '',
        });

        const systemSettings = reactive({
            saving: false,
            message: '',
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
            imageUrls: '',
            videoUrls: '',
            audioUrls: '',
            generateAudio: true,
        });

        const imageModelOptions = Object.keys(IMAGE_MODEL_CAPS);
        const videoModelOptions = Object.keys(VIDEO_MODEL_CAPS);
        const currentImageCapability = computed(() => IMAGE_MODEL_CAPS[image.model] || IMAGE_MODEL_CAPS['gemini-3-pro-image-preview']);
        const imageAspectRatios = computed(() => currentImageCapability.value.ratios);
        const imageSizeOptions = computed(() => currentImageCapability.value.sizes || IMAGE_SIZE_OPTIONS);
        const imageSupportsQuality = computed(() => currentImageCapability.value.supportsQuality === true);
        const currentVideoCapability = computed(() => VIDEO_MODEL_CAPS[video.model] || VIDEO_MODEL_CAPS.sora2);
        const videoSupportsResolution = computed(() => currentVideoCapability.value.exposesResolution !== false);
        const videoDurationOptions = computed(() => currentVideoCapability.value.durations || []);
        const videoDurationRange = computed(() => currentVideoCapability.value.durationRange || null);
        const videoDurationMin = computed(() => videoDurationRange.value ? videoDurationRange.value[0] : null);
        const videoDurationMax = computed(() => videoDurationRange.value ? videoDurationRange.value[1] : null);

        const defaultImageSize = (sizes) => sizes.includes('2K') ? '2K' : sizes[0];

        const normalizeImageSettings = () => {
            if (!IMAGE_MODEL_CAPS[image.model]) image.model = 'gemini-3-pro-image-preview';
            const cap = currentImageCapability.value;
            if (!cap.ratios.includes(image.aspectRatio)) image.aspectRatio = cap.ratios[0];
            const sizes = cap.sizes || IMAGE_SIZE_OPTIONS;
            if (!sizes.includes(image.size)) image.size = defaultImageSize(sizes);
        };

        const normalizeVideoSettings = () => {
            if (!VIDEO_MODEL_CAPS[video.model]) video.model = 'sora2';
            const cap = currentVideoCapability.value;
            if (!cap.ratios.includes(video.aspectRatio)) video.aspectRatio = cap.ratios[0];
            const resolutions = cap.resolutions || [];
            if (videoSupportsResolution.value && resolutions.length && !resolutions.includes(video.resolution)) {
                video.resolution = resolutions[0];
            } else if (!videoSupportsResolution.value) {
                video.resolution = '';
            }
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
            if (cap.usesReferenceUrlArrays) {
                video.videoReference = '';
            } else {
                video.videoUrls = '';
                video.audioUrls = '';
                videoReferenceFiles.value = [];
                audioReferenceFiles.value = [];
            }
        };

        onMounted(async () => {
            const authenticated = await loadAuthStatus();
            if (!authenticated) return;
            await loadSavedConfig();
            configLoaded.value = true;
            await loadPersistedTasks();
        });

        const loadAuthStatus = async () => {
            try {
                const res = await fetch('/api/auth/status');
                const data = await res.json();
                if (!res.ok || !data.authenticated) {
                    window.location.href = '/login';
                    return false;
                }
                if (data.username) {
                    authSettings.username = data.username;
                }
                return true;
            } catch (e) {
                window.location.href = '/login';
                return false;
            }
        };

        const logout = async () => {
            await fetch('/api/auth/logout', { method: 'POST' }).catch(() => {});
            activePolls.forEach(interval => clearInterval(interval));
            activePolls.clear();
            configLoaded.value = false;
            showSystemConfig.value = false;
            results.value = [];
            currentResult.value = null;
            currentLogs.value = [];
            window.location.href = '/login';
        };

        const updateAuth = async () => {
            authSettings.message = '';
            const res = await fetch('/api/auth/update', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    username: authSettings.username,
                    password: authSettings.newPassword,
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.message || '保存登录配置失败');
            authSettings.username = data.username || authSettings.username;
            authSettings.newPassword = '';
            authSettings.message = '登录配置已保存。';
            return data;
        };

        const openSystemConfig = () => {
            systemSettings.message = '';
            showSystemConfig.value = true;
        };

        const closeSystemConfig = () => {
            showSystemConfig.value = false;
        };

        const serializeConfig = () => ({
            baseUrl: config.baseUrl,
            publicMediaBaseUrl: config.publicMediaBaseUrl,
            enableProxy: config.enableProxy,
            proxyUrl: config.proxyUrl,
            gptImage2ApiKey: config.gptImage2ApiKey,
            gptImage25FlareApiKey: config.gptImage25FlareApiKey,
            gptImage25SunburstApiKey: config.gptImage25SunburstApiKey,
            seedreamImageApiKey: config.seedreamImageApiKey,
            gemini3ProImageApiKey: config.gemini3ProImageApiKey,
            gemini31FlashImageApiKey: config.gemini31FlashImageApiKey,
            gemini31FlashLiteImageApiKey: config.gemini31FlashLiteImageApiKey,
            videoApiKeys: { ...config.videoApiKeys },
        });

        const saveCurrentConfig = async () => {
            clearTimeout(saveConfigTimer);
            const payload = JSON.stringify({ config: serializeConfig(), image, video,
                referenceImages: Object.fromEntries(['image', 'video'].map(target => [target,
                    referenceImages[target].filter(item => item.status === 'ready').map(item => ({
                        id: item.id, source: item.source, name: item.name, url: item.url,
                        thumbnailUrl: item.thumbnailUrl, sourceUrl: item.sourceUrl,
                    }))])) });
            const res = await fetch('/api/config', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: payload,
            });
            if (!res.ok) {
                const data = await res.json().catch(() => ({}));
                throw new Error(data.message || '保存配置失败');
            }
        };

        const saveSystemConfig = async () => {
            systemSettings.saving = true;
            systemSettings.message = '';
            try {
                await saveCurrentConfig();
                await updateAuth();
                systemSettings.message = '系统配置已保存。';
            } catch (e) {
                systemSettings.message = e.message || '保存系统配置失败';
            } finally {
                systemSettings.saving = false;
            }
        };

        const applySavedConfig = (savedData) => {
            const savedConfig = savedData.config || {};
            ['baseUrl', 'publicMediaBaseUrl', 'enableProxy', 'proxyUrl', 'gptImage2ApiKey', 'gptImage25FlareApiKey', 'gptImage25SunburstApiKey', 'seedreamImageApiKey', 'gemini3ProImageApiKey', 'gemini31FlashImageApiKey', 'gemini31FlashLiteImageApiKey'].forEach(key => {
                if (Object.prototype.hasOwnProperty.call(savedConfig, key)) {
                    config[key] = savedConfig[key];
                }
            });
            if (savedConfig.videoApiKeys && typeof savedConfig.videoApiKeys === 'object' && !Array.isArray(savedConfig.videoApiKeys)) {
                Object.keys(VIDEO_MODEL_CAPS).forEach(model => {
                    if (Object.prototype.hasOwnProperty.call(savedConfig.videoApiKeys, model)) {
                        config.videoApiKeys[model] = savedConfig.videoApiKeys[model] || '';
                    }
                });
            }
            Object.assign(image, savedData.image || {});
            Object.assign(video, savedData.video || {});
            normalizeImageSettings();
            normalizeVideoSettings();
            ['image', 'video'].forEach(target => {
                const saved = savedData.referenceImages?.[target];
                if (Array.isArray(saved)) {
                    referenceImages[target] = saved.filter(item => item.url?.startsWith('/downloads/'))
                        .map(item => ({ ...item, status: 'ready' }));
                    syncReferenceUrls(target);
                } else {
                    const source = target === 'image' ? image : video;
                    const urls = source.imageUrls;
                    source.imageUrls = '';
                    addReferenceUrls(target, urls);
                }
            });
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


        const loadPersistedTasks = async (page = currentPage.value) => {
            try {
                const params = new URLSearchParams({
                    page: String(page),
                    page_size: String(pageSize),
                    type: galleryFilter.value,
                });
                const res = await fetch(`/api/tasks?${params.toString()}`);
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || '加载任务失败');
                results.value = (data.tasks || []).map(item => ({
                    id: item.id || item.internal_task_id,
                    taskId: item.taskId || item.internal_task_id || item.id,
                    url: item.url || item.image_url || item.media_url || item.local_url,
                    thumbnailUrl: item.thumbnailUrl || item.thumbnail_url || null,
                    logs: item.logs?.length ? item.logs : [`[系统] 已从数据库加载任务。`],
                    durationSeconds: item.durationSeconds ?? item.duration_seconds ?? null,
                    ...item,
                }));
                currentPage.value = data.page || page;
                totalItems.value = data.total || 0;
                totalPages.value = data.total_pages || 1;
                if (results.value.length) {
                    currentResult.value = results.value[0];
                    currentLogs.value = currentResult.value.logs || [];
                } else {
                    currentResult.value = null;
                    currentLogs.value = [];
                }
                results.value
                    .filter(item => (item.status === 'pending' || item.status === 'running') && item.taskId)
                    .forEach(item => pollTask(item.taskId, item.id));
            } catch (e) {
                console.error('加载任务失败', e);
            }
        };

        const refreshTaskList = async () => {
            await loadPersistedTasks(currentPage.value);
        };

        const setGalleryFilter = async (filter) => {
            galleryFilter.value = filter;
            currentPage.value = 1;
            await loadPersistedTasks(1);
        };

        watch([config, image, video, referenceImages], () => {
            if (!configLoaded.value) return;
            clearTimeout(saveConfigTimer);
            saveConfigTimer = setTimeout(() => {
                saveCurrentConfig().catch(e => console.error('保存数据库配置失败', e));
            }, 400);
        }, { deep: true });

        watch(() => video.model, normalizeVideoSettings);
        watch(() => image.model, normalizeImageSettings);

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
                'gpt-image-2.5-flare': config.gptImage25FlareApiKey,
                'gpt-image-2.5-sunburst': config.gptImage25SunburstApiKey,
                'seedream-5-pro': config.seedreamImageApiKey,
                'gemini-3-pro-image-preview': config.gemini3ProImageApiKey,
                'gemini-3.1-flash-image-preview': config.gemini31FlashImageApiKey,
                'gemini-3.1-flash-lite-image': config.gemini31FlashLiteImageApiKey,
            };
            return keyByModel[model] || '';
        };

        const resolveVideoApiKey = (model) => config.videoApiKeys[model] || '';

        const appendCommonTaskFields = (formData, source, apiKey, options = {}) => {
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

        const onVideoReferenceFilesChange = (event) => {
            videoReferenceFiles.value = Array.from(event.target.files || []);
        };

        const onAudioReferenceFilesChange = (event) => {
            audioReferenceFiles.value = Array.from(event.target.files || []);
        };

        const removeVideoReferenceFile = (index) => {
            videoReferenceFiles.value.splice(index, 1);
        };

        const removeAudioReferenceFile = (index) => {
            audioReferenceFiles.value.splice(index, 1);
        };

        const onResultDragStart = (event, item) => {
            if (item.status !== 'completed' || item.type !== 'image' || !item.url) {
                event.preventDefault();
                return;
            }
            event.dataTransfer.effectAllowed = 'copy';
            event.dataTransfer.setData('text/plain', item.url);
            event.dataTransfer.setData('application/x-media-tester-url', item.url);
            event.dataTransfer.setData('application/x-media-tester-image', JSON.stringify({
                url: item.url, thumbnailUrl: item.thumbnailUrl || item.thumbnail_url,
                name: item.filename || item.model,
            }));
        };

        const onReferenceDrop = (event, targetName) => {
            if (event.dataTransfer.files.length) {
                addReferenceFiles(targetName, Array.from(event.dataTransfer.files));
                return;
            }
            const gallery = event.dataTransfer.getData('application/x-media-tester-image');
            if (gallery) {
                try {
                    const item = JSON.parse(gallery);
                    if (referenceImages[targetName].some(ref => ref.url === item.url)) return;
                    if (item.thumbnailUrl && item.url?.startsWith('/downloads/')) {
                        referenceImages[targetName].push({ ...item, id: crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,
                            source: 'gallery', status: 'ready' });
                        syncReferenceUrls(targetName);
                        return;
                    }
                } catch (_) { /* Fall back to importing the dropped URL. */ }
            }
            addReferenceUrls(targetName, event.dataTransfer.getData('application/x-media-tester-url') || event.dataTransfer.getData('text/plain'));
        };

        const syncReferenceUrls = (target) => {
            (target === 'image' ? image : video).imageUrls = referenceImages[target]
                .filter(item => item.status === 'ready').map(item => item.url).join('\n');
        };

        const importReference = async (target, item) => {
            if (referenceControllers.has(item.id)) return;
            const controller = new AbortController();
            referenceControllers.set(item.id, controller);
            item.status = 'loading';
            item.error = '';
            try {
                const payload = new FormData();
                if (item.file) payload.append('image_file', item.file);
                else payload.append('url', item.sourceUrl);
                payload.append('proxy_url', config.enableProxy ? config.proxyUrl : '');
                const res = await fetch('/api/reference-images', { method: 'POST', body: payload, signal: controller.signal });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '参考图片导入失败');
                if (!referenceImages[target].some(ref => ref.id === item.id)) return;
                if (item.localPreview) URL.revokeObjectURL(item.localPreview);
                Object.assign(item, { url: data.url, thumbnailUrl: data.thumbnailUrl,
                    status: 'ready', localPreview: null, file: null });
                syncReferenceUrls(target);
            } catch (error) {
                if (controller.signal.aborted) return;
                item.status = 'error';
                item.error = error.message || '导入失败';
            } finally {
                referenceControllers.delete(item.id);
            }
        };

        const addReferenceFiles = (target, files) => {
            files.forEach(file => {
                const key = `${file.name}:${file.size}:${file.lastModified}`;
                if (referenceImages[target].some(item => item.fileKey === key)) return;
                const invalid = !file.type.startsWith('image/') || file.size > 20 * 1024 * 1024;
                const item = reactive({ id: `${Date.now()}-${Math.random()}`, source: 'local',
                    name: file.name, file, fileKey: key, status: invalid ? 'error' : 'loading',
                    error: invalid ? '请选择不超过 20 MB 的图片' : '',
                    localPreview: invalid ? null : URL.createObjectURL(file) });
                referenceImages[target].push(item);
                if (!invalid) importReference(target, item);
            });
        };

        const addReferenceUrls = (target, text) => {
            String(text || '').split(/\r?\n/).map(url => url.trim()).filter(Boolean).forEach(url => {
                if (referenceImages[target].some(item => item.sourceUrl === url || item.url === url)) return;
                const item = reactive({ id: `${Date.now()}-${Math.random()}`, source: 'url',
                    name: url.split('/').pop()?.split('?')[0] || 'URL 图片', sourceUrl: url, status: 'loading' });
                referenceImages[target].push(item);
                importReference(target, item);
            });
        };

        const removeReference = (target, id) => {
            const item = referenceImages[target].find(item => item.id === id);
            referenceControllers.get(id)?.abort();
            if (item?.localPreview) URL.revokeObjectURL(item.localPreview);
            referenceImages[target] = referenceImages[target].filter(item => item.id !== id);
            syncReferenceUrls(target);
        };

        const validateReferenceImages = (target) => {
            if (referenceImages[target].some(item => item.status === 'loading')) {
                throw new Error('参考图片正在上传或下载，请等待完成后再生成。');
            }
            if (referenceImages[target].some(item => item.status !== 'ready')) {
                throw new Error('有参考图片导入失败，请重试或移除后再生成。');
            }
        };

        onBeforeUnmount(() => {
            referenceControllers.forEach(controller => controller.abort());
            Object.values(referenceImages).flat().forEach(item => {
                if (item.localPreview) URL.revokeObjectURL(item.localPreview);
            });
        });

        const createPlaceholder = (item) => {
            const startedAtMs = Date.now();
            const result = {
                id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
                taskId: null,
                status: 'starting',
                url: null,
                error: null,
                logs: [],
                createdAt: new Date().toLocaleString(),
                startedAtMs,
                finishedAtMs: null,
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

        const resetPreviewImageTransform = () => {
            previewImageScale.value = 1;
            previewImagePan.x = 0;
            previewImagePan.y = 0;
            previewImageDrag.active = false;
            previewImageDrag.pointerId = null;
            previewImageTransformOrigin.value = 'center center';
        };

        const openPreview = (item) => {
            currentResult.value = item;
            currentLogs.value = item.logs || [];
            resetPreviewImageTransform();
            if ((item.status === 'completed' && item.url) || item.status === 'error' || item.status === 'failed') {
                selectedResult.value = item;
            }
            scrollLogs();
        };

        const onPreviewImageWheel = (event) => {
            if (selectedResult.value?.type !== 'image') return;
            const imageEl = event.currentTarget.querySelector('img');
            if (imageEl) {
                const rect = imageEl.getBoundingClientRect();
                const x = rect.width ? ((event.clientX - rect.left) / rect.width) * 100 : 50;
                const y = rect.height ? ((event.clientY - rect.top) / rect.height) * 100 : 50;
                previewImageTransformOrigin.value = `${Math.min(100, Math.max(0, x))}% ${Math.min(100, Math.max(0, y))}%`;
            }
            const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
            const nextScale = previewImageScale.value * factor;
            previewImageScale.value = Math.round(Math.min(5, Math.max(0.5, nextScale)) * 100) / 100;
            if (previewImageScale.value <= 1) {
                previewImagePan.x = 0;
                previewImagePan.y = 0;
                previewImageDrag.active = false;
                previewImageDrag.pointerId = null;
            }
        };

        const startPreviewImageDrag = (event) => {
            if (selectedResult.value?.type !== 'image' || previewImageScale.value <= 1) return;
            event.preventDefault();
            previewImageDrag.active = true;
            previewImageDrag.pointerId = event.pointerId;
            previewImageDrag.startX = event.clientX;
            previewImageDrag.startY = event.clientY;
            previewImageDrag.startPanX = previewImagePan.x;
            previewImageDrag.startPanY = previewImagePan.y;
            event.currentTarget.setPointerCapture?.(event.pointerId);
        };

        const movePreviewImageDrag = (event) => {
            if (!previewImageDrag.active || previewImageDrag.pointerId !== event.pointerId) return;
            event.preventDefault();
            previewImagePan.x = previewImageDrag.startPanX + event.clientX - previewImageDrag.startX;
            previewImagePan.y = previewImageDrag.startPanY + event.clientY - previewImageDrag.startY;
        };

        const endPreviewImageDrag = (event) => {
            if (!previewImageDrag.active || previewImageDrag.pointerId !== event.pointerId) return;
            event.currentTarget.releasePointerCapture?.(event.pointerId);
            previewImageDrag.active = false;
            previewImageDrag.pointerId = null;
        };

        const formatJson = (value) => {
            if (!value) return '';
            try {
                return JSON.stringify(value, null, 2);
            } catch (_) {
                return String(value);
            }
        };

        const formatTaskDuration = (item) => {
            if (!item || item.status !== 'completed') return '';
            if (item.durationSeconds !== null && item.durationSeconds !== undefined) {
                return `${Number(item.durationSeconds) || 0}s`;
            }
            if (!item.startedAtMs || !item.finishedAtMs) return '';
            const seconds = Math.max(0, Math.round((item.finishedAtMs - item.startedAtMs) / 1000));
            return `${seconds}s`;
        };

        const closePreview = () => {
            selectedResult.value = null;
            resetPreviewImageTransform();
        };

        const toggleLogs = () => {
            showLogs.value = !showLogs.value;
            if (showLogs.value) scrollLogs();
        };

        const closeLogs = () => {
            showLogs.value = false;
        };

        const nextPage = async () => {
            const next = Math.min(totalPages.value, currentPage.value + 1);
            if (next !== currentPage.value) await loadPersistedTasks(next);
        };

        const prevPage = async () => {
            const prev = Math.max(1, currentPage.value - 1);
            if (prev !== currentPage.value) await loadPersistedTasks(prev);
        };

        const runImageTask = async () => {
            const meta = imageSupportsQuality.value
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
            if (referenceImages.image.length) {
                placeholder.logs.push(`[输入] 已附加 ${referenceImages.image.length} 张参考图。`);
            }
            currentLogs.value = placeholder.logs;

            try {
                validateReferenceImages('image');
                const payload = new FormData();
                appendCommonTaskFields(payload, image, resolveImageApiKey(image.model));
                payload.append('image_size', image.size);
                if (imageSupportsQuality.value) {
                    payload.append('quality', image.quality || 'medium');
                }

                const res = await fetch('/api/image', { method: 'POST', body: payload });
                const data = await res.json();
                if (!res.ok) throw new Error(data.message || '图片任务启动失败');

                updateResult(placeholder.id, {
                    taskId: data.internal_task_id,
                    status: 'running',
                    logs: [...placeholder.logs, `[系统] 后端已接收任务：${data.internal_task_id}。`],
                });
                pollTask(data.internal_task_id, placeholder.id);
                if (galleryFilter.value !== 'all' && galleryFilter.value !== 'image') {
                    await loadPersistedTasks(currentPage.value);
                }
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
            const metaParts = [`${video.duration}s`, video.aspectRatio];
            if (videoSupportsResolution.value && video.resolution) metaParts.push(video.resolution);
            const meta = metaParts.join(' · ');
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
            if (!usingStartEnd && referenceImages.video.length) {
                placeholder.logs.push(`[输入] 已附加 ${referenceImages.video.length} 张参考图。`);
            }
            if (videoReferenceFiles.value.length) {
                placeholder.logs.push(`[输入] 已附加 ${videoReferenceFiles.value.length} 个本地参考视频。`);
            }
            if (audioReferenceFiles.value.length) {
                placeholder.logs.push(`[输入] 已附加 ${audioReferenceFiles.value.length} 个本地参考音频。`);
            }
            currentLogs.value = placeholder.logs;

            try {
                if (!usingStartEnd) validateReferenceImages('video');
                const imageCount = videoReferenceCount.value;
                if (!usingStartEnd && imageCount > cap.maxRefs) {
                    throw new Error(`当前模型最多支持 ${cap.maxRefs} 张参考图。`);
                }
                if (cap.usesReferenceUrlArrays) {
                    const videoCount = videoUrlReferenceCount.value;
                    const audioCount = audioUrlReferenceCount.value;
                    if (videoCount > cap.maxVideoRefs) {
                        throw new Error(`当前模型最多支持 ${cap.maxVideoRefs} 个参考视频。`);
                    }
                    if (audioCount > cap.maxAudioRefs) {
                        throw new Error(`当前模型最多支持 ${cap.maxAudioRefs} 个参考音频。`);
                    }
                    if (totalVideoMediaReferenceCount.value > cap.maxTotalRefs) {
                        throw new Error(`当前模型的参考素材合计最多 ${cap.maxTotalRefs} 个。`);
                    }
                    if (audioCount > 0 && imageCount === 0 && videoCount === 0) {
                        throw new Error('参考音频必须搭配至少一张参考图片或一个参考视频。');
                    }
                    const hasLocalReferences = (!usingStartEnd && referenceImages.video.some(item => item.url?.startsWith('/downloads/')))
                        || videoReferenceFiles.value.length
                        || audioReferenceFiles.value.length;
                    if (hasLocalReferences && !String(config.publicMediaBaseUrl || '').trim()) {
                        throw new Error('上传本地参考素材前，请先在系统配置中填写参考素材公网地址。');
                    }
                }
                const payload = new FormData();
                appendCommonTaskFields(payload, video, resolveVideoApiKey(video.model), { includeReferences: !usingStartEnd });
                payload.append('public_media_base_url', String(config.publicMediaBaseUrl || '').trim());
                payload.append('duration', video.duration);
                if (videoSupportsResolution.value && video.resolution) {
                    payload.append('resolution', video.resolution);
                }
                payload.append('create_path', video.createPath);
                payload.append('status_path', video.statusPath);
                if (cap.supportsStartEnd) {
                    if (video.startFrame.trim()) payload.append('start_frame', video.startFrame.trim());
                    if (video.endFrame.trim()) payload.append('end_frame', video.endFrame.trim());
                }
                if (cap.usesReferenceUrlArrays) {
                    video.videoUrls.split('\n').map(s => s.trim()).filter(Boolean).forEach(url => {
                        payload.append('video_url', url);
                    });
                    video.audioUrls.split('\n').map(s => s.trim()).filter(Boolean).forEach(url => {
                        payload.append('audio_url', url);
                    });
                    videoReferenceFiles.value.forEach(file => payload.append('video_file', file));
                    audioReferenceFiles.value.forEach(file => payload.append('audio_file', file));
                    payload.append('generate_audio', video.generateAudio ? 'true' : 'false');
                } else if (cap.supportsVideoReference && video.videoReference.trim()) {
                    payload.append('video_reference', video.videoReference.trim());
                    payload.append('video_reference_field', cap.videoReferenceField || 'video_reference');
                }

                const res = await fetch('/api/video', { method: 'POST', body: payload });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || data.message || '视频任务启动失败');

                updateResult(placeholder.id, {
                    taskId: data.internal_task_id,
                    status: 'running',
                    logs: [...placeholder.logs, `[系统] 后端已接收任务：${data.internal_task_id}。`],
                });
                pollTask(data.internal_task_id, placeholder.id);
                if (galleryFilter.value !== 'all' && galleryFilter.value !== 'video') {
                    await loadPersistedTasks(currentPage.value);
                }
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
            if (activePolls.has(taskId)) return;
            const pollInterval = setInterval(async () => {
                try {
                    const res = await fetch(`/api/task/${taskId}`);
                    const data = await res.json();
                    if (!res.ok) throw new Error(data.message || '任务状态查询失败');

                    const item = results.value.find(result => result.id === resultId);
                    if (!item) {
                        clearInterval(pollInterval);
                        activePolls.delete(taskId);
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
                        thumbnailUrl: data.thumbnailUrl || data.thumbnail_url || data.asset?.thumbnailUrl || data.asset?.thumbnail_url || item.thumbnailUrl,
                        thumbnail_url: data.thumbnail_url || data.thumbnailUrl || data.asset?.thumbnail_url || data.asset?.thumbnailUrl || item.thumbnail_url,
                        durationSeconds: data.durationSeconds ?? data.duration_seconds ?? item.durationSeconds,
                    };

                    if (data.status === 'completed') {
                        patch.url = data.asset?.url || (item.type === 'image' ? data.image_url : data.media_url);
                        patch.filename = data.asset?.filename || item.filename;
                        patch.remote_url = data.asset?.remote_url || data.remote_url || item.remote_url;
                        patch.createdAt = data.asset?.createdAt || item.createdAt;
                    }

                    if (data.status === 'completed' || data.status === 'failed' || data.status === 'error') {
                        patch.finishedAtMs = item.finishedAtMs || Date.now();
                    }

                    updateResult(resultId, patch);

                    if (data.status === 'completed' || data.status === 'failed' || data.status === 'error') {
                        clearInterval(pollInterval);
                        activePolls.delete(taskId);
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
            activePolls.set(taskId, pollInterval);
        };

        return {
            tab, isLoading, isSubmitting,
            authSettings, systemSettings,
            config, image, video,
            imageModelOptions, videoModelOptions, currentImageCapability, imageAspectRatios, imageSizeOptions, imageSupportsQuality, currentVideoCapability, videoSupportsResolution, videoDurationOptions, videoDurationRange, videoDurationMin, videoDurationMax,
            videoReferenceFiles, audioReferenceFiles,
            imageReferenceCount, videoReferenceCount, videoUrlReferenceCount, audioUrlReferenceCount, totalVideoMediaReferenceCount,
            onVideoReferenceFilesChange, onAudioReferenceFilesChange,
            removeVideoReferenceFile, removeAudioReferenceFile,
            onResultDragStart, onReferenceDrop,
            referenceImages, addReferenceFiles, addReferenceUrls, removeReference, importReference,
            results, paginatedResults, currentPage, totalPages, totalItems, galleryFilter, selectedResult, previewImageStyle, showLogs, showSystemConfig,
            openPreview, closePreview, toggleLogs, closeLogs, openSystemConfig, closeSystemConfig, saveSystemConfig, refreshTaskList, setGalleryFilter,
            onPreviewImageWheel, startPreviewImageDrag, movePreviewImageDrag, endPreviewImageDrag,
            logout, updateAuth,
            nextPage, prevPage,
            submitTask,
            currentLogs, currentResult, formatJson, formatTaskDuration
        };
    }
});

app.component('reference-images', {
    props: ['items', 'limit'],
    emits: ['files', 'urls', 'drop', 'remove', 'retry'],
    setup(props, { emit }) {
        const draft = ref('');
        const preview = ref(null);
        const selectFiles = (event) => {
            emit('files', Array.from(event.target.files || []));
            event.target.value = '';
        };
        const importUrls = () => {
            if (!draft.value.trim()) return;
            emit('urls', draft.value);
            draft.value = '';
        };
        return { draft, preview, selectFiles, importUrls };
    },
    template: `
        <section class="space-y-2" aria-label="参考图片">
            <div class="flex items-center justify-between text-xs">
                <span class="font-medium text-gray-300">参考图</span>
                <span class="text-indigo-300">{{ items.length }}{{ limit ? ' / ' + limit : '' }} 张</span>
            </div>
            <div v-if="items.length" class="grid grid-cols-3 gap-2">
                <div v-for="item in items" :key="item.id" class="relative rounded-lg border border-white/10 bg-black/20 overflow-hidden">
                    <button type="button" class="relative block aspect-square w-full overflow-hidden" :disabled="!item.thumbnailUrl && !item.localPreview" @click="preview = item" :aria-label="'预览 ' + item.name">
                        <img v-if="item.thumbnailUrl || item.localPreview" :src="item.thumbnailUrl || item.localPreview" class="h-full w-full object-cover" :alt="item.name">
                        <span v-else class="text-xs text-gray-500">图片</span>
                        <span v-if="item.status === 'loading'" class="absolute inset-0 flex items-center justify-center bg-black/60 text-xs text-indigo-200">{{ item.source === 'local' ? '上传中…' : '下载中…' }}</span>
                        <span v-if="item.status === 'error'" class="absolute inset-0 flex items-center justify-center bg-red-950/80 text-xs text-red-200">导入失败</span>
                    </button>
                    <button type="button" @click.stop="$emit('remove', item.id)" class="absolute right-1 top-1 flex h-6 w-6 items-center justify-center rounded-full bg-black/75 text-white hover:bg-red-600" :aria-label="'移除 ' + item.name" title="取消参考">×</button>
                    <div class="p-1.5">
                        <p class="truncate text-[10px] text-gray-300" :title="item.name">{{ item.name }}</p>
                        <p class="text-[10px] text-gray-500">{{ item.source === 'local' ? '本地图片' : item.source === 'gallery' ? '画廊图片' : 'URL 图片' }}</p>
                        <template v-if="item.status === 'error'">
                            <p class="break-words text-[10px] text-red-300">{{ item.error }}</p>
                            <button type="button" @click="$emit('retry', item)" class="mt-1 text-xs text-indigo-300 hover:text-white">重试</button>
                        </template>
                    </div>
                </div>
            </div>
            <label class="block cursor-pointer rounded-xl border border-dashed border-white/15 bg-white/[0.03] p-3 text-center transition hover:border-indigo-400/70" @dragover.prevent @drop.prevent="$emit('drop', $event)">
                <input type="file" class="hidden" multiple accept="image/jpeg,image/png,image/webp,image/gif" @change="selectFiles">
                <span class="text-xs text-gray-300">＋ 添加图片 / 拖入本地或画廊图片</span>
                <span class="mt-1 block text-[10px] text-gray-500">每张不超过 20 MB</span>
            </label>
            <textarea v-model="draft" class="glass-input text-xs resize-none" rows="2" aria-label="参考图片 URL" placeholder="粘贴图片 URL，每行一个"></textarea>
            <button type="button" @click="importUrls" :disabled="!draft.trim()" class="rounded-lg border border-white/10 bg-white/5 px-3 py-1.5 text-xs text-indigo-200 disabled:opacity-40">导入 URL</button>
            <Teleport to="body">
                <div v-if="preview" class="fixed inset-0 z-[100] flex items-center justify-center bg-black/85 p-8" @click.self="preview = null" @keydown.esc="preview = null">
                    <button type="button" @click="preview = null" class="absolute right-5 top-4 rounded-full bg-black/75 px-3 py-1 text-2xl text-white" aria-label="关闭参考图预览">×</button>
                    <img :src="preview.url || preview.localPreview || preview.thumbnailUrl" :alt="preview.name" class="max-h-[85vh] max-w-full object-contain">
                </div>
            </Teleport>
        </section>
    `,
});

app.mount('#app');
