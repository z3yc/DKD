package com.dkd.manage.service.impl;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

import com.dkd.common.ai.enums.AiModuleType;
import com.dkd.common.ai.enums.AiSuggestionType;
import com.dkd.common.ai.service.IAiService;
import com.dkd.common.utils.DateUtils;
import com.dkd.manage.domain.vo.NodeVo;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.dkd.manage.mapper.NodeMapper;
import com.dkd.manage.domain.Node;
import com.dkd.manage.service.INodeService;

/**
 * 点位管理Service业务层处理
 * 
 * @author ruoyi
 * @date 2025-10-28
 */
@Service
public class NodeServiceImpl implements INodeService 
{
    @Autowired
    private NodeMapper nodeMapper;
    
    @Autowired
    private IAiService aiService;

    /**
     * 查询点位管理
     * 
     * @param id 点位管理主键
     * @return 点位管理
     */
    @Override
    public Node selectNodeById(Long id)
    {
        return nodeMapper.selectNodeById(id);
    }

    /**
     * 查询点位管理列表
     * 
     * @param node 点位管理
     * @return 点位管理
     */
    @Override
    public List<Node> selectNodeList(Node node)
    {
        return nodeMapper.selectNodeList(node);
    }

    /**
     * 新增点位管理
     * 
     * @param node 点位管理
     * @return 结果
     */
    @Override
    public int insertNode(Node node)
    {
        node.setCreateTime(DateUtils.getNowDate());
        return nodeMapper.insertNode(node);
    }

    /**
     * 修改点位管理
     * 
     * @param node 点位管理
     * @return 结果
     */
    @Override
    public int updateNode(Node node)
    {
        node.setUpdateTime(DateUtils.getNowDate());
        return nodeMapper.updateNode(node);
    }

    /**
     * 批量删除点位管理
     * 
     * @param ids 需要删除的点位管理主键
     * @return 结果
     */
    @Override
    public int deleteNodeByIds(Long[] ids)
    {
        return nodeMapper.deleteNodeByIds(ids);
    }

    /**
     * 删除点位管理信息
     * 
     * @param id 点位管理主键
     * @return 结果
     */
    @Override
    public int deleteNodeById(Long id)
    {
        return nodeMapper.deleteNodeById(id);
    }

    /**
     * 查询点位管理列表
     *
     * @param node
     * @return NodeVo集合
     */
    @Override
    public List<NodeVo> selectNodeVoList(Node node) {
        return nodeMapper.selectNodeVoList(node);
    }
    
    @Override
    public String getNodeAnalysis(Long nodeId) {
        // 获取点位信息
        Node node = selectNodeById(nodeId);
        if (node == null) {
            return "未找到点位信息";
        }
        
        // 构建点位信息Map
        Map<String, Object> nodeData = new HashMap<>();
        nodeData.put("id", node.getId());
        nodeData.put("nodeName", node.getNodeName());
        nodeData.put("address", node.getAddress());
        nodeData.put("businessType", node.getBusinessType());
        nodeData.put("regionId", node.getRegionId());
        nodeData.put("partnerId", node.getPartnerId());
        
        // 调用AI服务进行点位分析
        return aiService.generateSuggestion(
            AiModuleType.NODE.getCode(), // 使用NODE模块类型进行点位分析
            nodeData, 
            AiSuggestionType.ANALYSIS.getCode()// 使用分析建议类型
        );
    }
    
    @Override
    public String getLocationSuggestion(Node node) {
        // 构建点位信息Map
        Map<String, Object> nodeData = new HashMap<>();
        nodeData.put("nodeName", node.getNodeName());
        nodeData.put("address", node.getAddress());
        nodeData.put("businessType", node.getBusinessType());
        nodeData.put("regionId", node.getRegionId());
        nodeData.put("partnerId", node.getPartnerId());
        
        // 调用AI服务进行选址建议
        return aiService.generateSuggestion(
            AiModuleType.NODE.getCode(), // 使用NODE模块类型进行点位分析
            nodeData, 
            AiSuggestionType.OPTIMIZATION.getCode() // 使用优化建议类型
        );
    }
    
    @Override
    public String importNode(List<Node> nodeList, boolean isUpdateSupport) {
        if (nodeList == null || nodeList.isEmpty()) {
            throw new RuntimeException("导入数据不能为空！");
        }
        int successNum = 0;
        int failureNum = 0;
        StringBuilder successMsg = new StringBuilder();
        StringBuilder failureMsg = new StringBuilder();

        // P1-10 判重 N+1 整改：原实现每行触发一次按名称查询，导入 N 行 = N 次 SQL。
        // 改为循环前一次预载建 Map（点位量级远小于导入批次时是净收益），循环内 O(1) 判重；
        // putIfAbsent 保持与原 findFirst 相同的「列表首个同名记录」语义。
        Map<String, Node> existingByName = new HashMap<>();
        for (Node existing : nodeMapper.selectNodeList(new Node())) {
            existingByName.putIfAbsent(existing.getNodeName(), existing);
        }

        for (Node node : nodeList) {
            try {
                // 验证数据的必要字段
                if (node.getNodeName() == null || node.getNodeName().trim().equals("")) {
                    failureNum++;
                    failureMsg.append("<br/>" + failureNum + "、点位名称不能为空");
                    continue;
                }
                if (node.getAddress() == null || node.getAddress().trim().equals("")) {
                    failureNum++;
                    failureMsg.append("<br/>" + failureNum + "、详细地址不能为空");
                    continue;
                }

                // 判重走预载索引（原为逐行查库）
                Node existNode = existingByName.get(node.getNodeName());

                if (existNode != null && !isUpdateSupport) {
                    failureNum++;
                    failureMsg.append("<br/>" + failureNum + "、点位名称：" + node.getNodeName() + " 已存在");
                } else {
                    if (existNode != null) {
                        // 更新现有记录
                        node.setId(existNode.getId());
                        updateNode(node);
                        successNum++;
                        successMsg.append("<br/>" + successNum + "、点位名称：" + node.getNodeName() + " 更新成功");
                    } else {
                        // 新增记录
                        insertNode(node);
                        successNum++;
                        successMsg.append("<br/>" + successNum + "、点位名称：" + node.getNodeName() + " 导入成功");
                    }
                    // 维护预载索引，保持原「逐行查库」在批内重复名称上的可见性：
                    // 同一批次里第二条同名点位仍会被判为「已存在」，不需要额外查库
                    existingByName.put(node.getNodeName(), node);
                }
            } catch (Exception e) {
                failureNum++;
                String msg = "<br/>" + failureNum + "、点位名称：" + node.getNodeName() + " 导入失败：" + e.getMessage();
                failureMsg.append(msg);
            }
        }
        
        if (failureNum > 0) {
            failureMsg.insert(0, "抱歉，导入失败！共 " + failureNum + " 条数据格式不正确，错误如下：");
            throw new RuntimeException(failureMsg.toString());
        } else {
            successMsg.insert(0, "恭喜，数据已全部导入成功！共 " + successNum + " 条");
            return successMsg.toString();
        }
    }
}
