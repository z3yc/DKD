import request from '@/utils/request'

// 获取点位AI分析建议
export function getNodeAnalysis(id) {
    return request({
        url: `/manage/node/analysis/${id}`,
        method: 'get'
    })
}

// 获取点位AI选址建议
export function getLocationSuggestion(data) {
    return request({
        url: '/manage/node/location-suggestion',
        method: 'post',
        data: data
    })
}

// 通用AI诊断接口
export function diagnose(moduleType, data) {
    return request({
        url: '/common/ai/diagnose',
        method: 'post',
        params: { moduleType },
        data: data
    })
}

// 通用AI问答接口
export function askAiQuestion(data) {
    return request({
        url: '/common/ai/ask',
        method: 'post',
        data: data
    })
}

// 生成特定建议
export function generateSuggestion(moduleType, suggestionType, data) {
    return request({
        url: '/common/ai/suggest',
        method: 'post',
        params: {
            moduleType,
            suggestionType
        },
        data: data
    })
}
