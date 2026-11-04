package com.dkd.manage.domain.dto;

/**
 * 知识库文档入库入参（排期 3-6，S11）。
 *
 * <p>Java 只做代理，切片/向量化/增量重建全在 Python（知识库是智能体自有数据，AGENTS §7.3）。
 * 本 DTO 的字段与 Python {@code LlmPingRequest} 同风格：对外 camelCase，内容原样透传。
 *
 * <p><b>为什么没有 operator 字段：</b>操作者必须取**当前登录人**（{@code getUsername()}），
 * 不能由请求体决定——否则任何人都能伪造"是谁入库了这份语料"，审计就失去意义。
 *
 * @author ruoyi
 * @date 2026-11-04
 */
public class AgentKbDocumentDto
{
    /** 文档编码 ^[a-z0-9][a-z0-9_\-]{2,63}$（UK） */
    private String docCode;

    /** 标题 1~200 字 */
    private String title;

    /** 分类：manual/fault_code/sop/faq/other */
    private String category;

    /** 来源类型（可选，如 manual/sop/upload） */
    private String sourceType;

    /** 来源引用（可选，如文件名/章节） */
    private String sourceRef;

    /** 正文（必填；Python 侧切片，≤1MB） */
    private String content;

    public String getDocCode()
    {
        return docCode;
    }

    public void setDocCode(String docCode)
    {
        this.docCode = docCode;
    }

    public String getTitle()
    {
        return title;
    }

    public void setTitle(String title)
    {
        this.title = title;
    }

    public String getCategory()
    {
        return category;
    }

    public void setCategory(String category)
    {
        this.category = category;
    }

    public String getSourceType()
    {
        return sourceType;
    }

    public void setSourceType(String sourceType)
    {
        this.sourceType = sourceType;
    }

    public String getSourceRef()
    {
        return sourceRef;
    }

    public void setSourceRef(String sourceRef)
    {
        this.sourceRef = sourceRef;
    }

    public String getContent()
    {
        return content;
    }

    public void setContent(String content)
    {
        this.content = content;
    }
}
