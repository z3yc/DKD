package com.dkd.manage.service;

import java.util.List;
import com.dkd.manage.domain.Node;
import com.dkd.manage.domain.vo.NodeVo;

/**
 * 点位管理Service接口
 * 
 * @author ruoyi
 * @date 2025-10-28
 */
public interface INodeService 
{
    /**
     * 查询点位管理
     * 
     * @param id 点位管理主键
     * @return 点位管理
     */
    public Node selectNodeById(Long id);

    /**
     * 查询点位管理列表
     * 
     * @param node 点位管理
     * @return 点位管理集合
     */
    public List<Node> selectNodeList(Node node);

    /**
     * 新增点位管理
     * 
     * @param node 点位管理
     * @return 结果
     */
    public int insertNode(Node node);

    /**
     * 修改点位管理
     * 
     * @param node 点位管理
     * @return 结果
     */
    public int updateNode(Node node);

    /**
     * 批量删除点位管理
     * 
     * @param ids 需要删除的点位管理主键集合
     * @return 结果
     */
    public int deleteNodeByIds(Long[] ids);

    /**
     * 删除点位管理信息
     * 
     * @param id 点位管理主键
     * @return 结果
     */
    public int deleteNodeById(Long id);

    /**
     * 查询点位管理列表
     * @param node
     * @return NodeVo集合
     */
    public List<NodeVo> selectNodeVoList(Node node);
    
    /**
     * 获取点位AI分析建议
     * @param nodeId 点位ID
     * @return AI分析结果
     */
    public String getNodeAnalysis(Long nodeId);
    
    /**
     * 获取点位AI选址建议
     * @param node 点位信息
     * @return AI选址建议
     */
    public String getLocationSuggestion(Node node);
    
    /**
     * 点位管理数据导入
     * @param nodeList 点位数据列表
     * @param isUpdateSupport 是否更新支持，如果已存在，则进行更新数据
     * @return 结果
     */
    public String importNode(List<Node> nodeList, boolean isUpdateSupport);
}
