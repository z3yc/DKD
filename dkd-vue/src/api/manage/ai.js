import request from '@/utils/request'

// 获取AI智能诊断
export function getAiDiagnosis(innerCode) {
    return request({
        url: '/manage/ai/diagnose',
        method: 'get',
        params: { innerCode }
    })
}