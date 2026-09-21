package com.dkd.manage.controller;

import java.io.IOException;
import java.util.List;
import javax.servlet.http.HttpServletResponse;

import com.dkd.manage.domain.vo.NodeVo;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import com.dkd.common.annotation.Log;
import com.dkd.common.core.controller.BaseController;
import com.dkd.common.core.domain.AjaxResult;
import com.dkd.common.enums.BusinessType;
import com.dkd.manage.domain.Node;
import com.dkd.manage.service.INodeService;
import com.dkd.common.utils.poi.ExcelUtil;
import com.dkd.common.core.page.TableDataInfo;
import org.springframework.web.multipart.MultipartFile;

/**
 * 点位管理Controller
 * 
 * @author ruoyi
 * @date 2025-10-28
 */
@RestController
@RequestMapping("/manage/node")
public class NodeController extends BaseController
{
    @Autowired
    private INodeService nodeService;

    /**
     * 查询点位管理列表
     */
    @PreAuthorize("@ss.hasPermi('manage:node:list')")
    @GetMapping("/list")
    public TableDataInfo list(Node node)
    {
        startPage();
        List<NodeVo> voList = nodeService.selectNodeVoList(node);
        return getDataTable(voList);
    }

    /**
     * 导出点位管理列表
     */
    @PreAuthorize("@ss.hasPermi('manage:node:export')")
    @Log(title = "点位管理", businessType = BusinessType.EXPORT)
    @PostMapping("/export")
    public void export(HttpServletResponse response, Node node)
    {
        startPage();
        List<NodeVo> voList = nodeService.selectNodeVoList(node);
        ExcelUtil<NodeVo> util = new ExcelUtil<NodeVo>(NodeVo.class);
        util.exportExcel(response, voList, "点位管理数据");
    }

    /**
     * 获取点位管理详细信息
     */
    @PreAuthorize("@ss.hasPermi('manage:node:query')")
    @GetMapping(value = "/{id}")
    public AjaxResult getInfo(@PathVariable("id") Long id)
    {
        return success(nodeService.selectNodeById(id));
    }

    /**
     * 新增点位管理
     */
    @PreAuthorize("@ss.hasPermi('manage:node:add')")
    @Log(title = "点位管理", businessType = BusinessType.INSERT)
    @PostMapping
    public AjaxResult add(@RequestBody Node node)
    {
        return toAjax(nodeService.insertNode(node));
    }

    /**
     * 修改点位管理
     */
    @PreAuthorize("@ss.hasPermi('manage:node:edit')")
    @Log(title = "点位管理", businessType = BusinessType.UPDATE)
    @PutMapping
    public AjaxResult edit(@RequestBody Node node)
    {
        return toAjax(nodeService.updateNode(node));
    }

    /**
     * 删除点位管理
     */
    @PreAuthorize("@ss.hasPermi('manage:node:remove')")
    @Log(title = "点位管理", businessType = BusinessType.DELETE)
	@DeleteMapping("/{ids}")
    public AjaxResult remove(@PathVariable Long[] ids)
    {
        return toAjax(nodeService.deleteNodeByIds(ids));
    }
    
    /**
     * 获取点位AI分析建议
     */
    @PreAuthorize("@ss.hasPermi('manage:node:query')")
    @GetMapping(value = "/analysis/{id}")
    public AjaxResult getNodeAnalysis(@PathVariable("id") Long id)
    {
        String analysis = nodeService.getNodeAnalysis(id);
        return AjaxResult.success("点位AI分析完成", analysis);
    }
    
    /**
     * 获取点位AI选址建议
     */
    @PreAuthorize("@ss.hasPermi('manage:node:add')")
    @PostMapping(value = "/location-suggestion")
    public AjaxResult getLocationSuggestion(@RequestBody Node node)
    {
        String suggestion = nodeService.getLocationSuggestion(node);
        return AjaxResult.success("选址建议生成完成", suggestion);
    }
    
    /**
     * 点位管理导入
     */
    @PreAuthorize("@ss.hasPermi('manage:node:add')")
    @Log(title = "点位管理", businessType = BusinessType.IMPORT)
    @PostMapping("/import")
    public AjaxResult nodeimportData(MultipartFile file, boolean updateSupport) throws Exception
    {
        ExcelUtil<Node> util = new ExcelUtil<Node>(Node.class);
        List<Node> nodeList = util.importExcel(file.getInputStream());
        String message = nodeService.importNode(nodeList, updateSupport);
        return AjaxResult.success(message);
    }
    
    /**
     * 下载点位管理导入模板
     */
    @PreAuthorize("@ss.hasPermi('manage:node:import')")
    @PostMapping("/importTemplate")
    public void importTemplate(HttpServletResponse response) throws IOException
    {
        ExcelUtil<Node> util = new ExcelUtil<Node>(Node.class);
        util.importTemplateExcel(response, "点位管理数据");
    }
    
    /**
     * 展示点位信息和区域信息
     */
    @PreAuthorize("@ss.hasPermi('manage:node:list')")
    @GetMapping("/nodeWithRegion")
    public TableDataInfo listNodesWithRegion()
    {
        startPage();
        List<NodeVo> voList = nodeService.selectNodeVoList(new Node());
        return getDataTable(voList);
    }
}