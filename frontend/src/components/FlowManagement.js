import React, { useState, useEffect } from 'react';
import {
  Table, Button, Space, Tag, Modal, Form, Input, InputNumber,
  message, Card, Typography, Divider, Popconfirm
} from 'antd';
import {
  PlusOutlined, EditOutlined, DeleteOutlined,
  CopyOutlined, EyeOutlined, PlayCircleOutlined
} from '@ant-design/icons';
import axios from 'axios';

const { Title } = Typography;

const FlowManagement = () => {
  const [flows, setFlows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [editingFlow, setEditingFlow] = useState(null);
  const [form] = Form.useForm();

  // Columns for the flows table
  const columns = [
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
      sorter: (a, b) => a.name.localeCompare(b.name),
    },
    {
      title: 'Date Created',
      dataIndex: 'created_at',
      key: 'created_at',
      sorter: (a, b) => new Date(a.created_at) - new Date(b.created_at),
    },
    {
      title: 'Last Modified',
      dataIndex: 'modified_at',
      key: 'modified_at',
      sorter: (a, b) => new Date(a.modified_at) - new Date(b.modified_at),
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      render: (status) => (
        <Tag color={status === 'active' ? 'green' : 'default'}>
          {status}
        </Tag>
      ),
      filters: [
        { text: 'Active', value: 'active' },
        { text: 'Inactive', value: 'inactive' },
      ],
      onFilter: (value, record) => record.status === value,
    },
    {
      title: 'Steps',
      dataIndex: 'steps',
      key: 'steps',
      render: (steps) => steps?.length || 0,
      sorter: (a, b) => (a.steps?.length || 0) - (b.steps?.length || 0),
    },
    {
      title: 'Actions',
      key: 'actions',
      className: 'flow-table-actions',
      render: (_, record) => (
        <Space size="middle">
          <Button icon={<EyeOutlined />} onClick={() => viewFlow(record)} />
          <Button icon={<EditOutlined />} onClick={() => editFlow(record)} />
          <Button icon={<CopyOutlined />} onClick={() => duplicateFlow(record.id)} />
          <Popconfirm
            title="Are you sure to delete this flow?"
            onConfirm={() => deleteFlow(record.id)}
            okText="Yes"
            cancelText="No"
          >
            <Button icon={<DeleteOutlined />} danger />
          </Popconfirm>
          <Button icon={<PlayCircleOutlined />} type="primary" onClick={() => playFlow(record.id)}>
            Play
          </Button>
        </Space>
      ),
    },
  ];

  // Fetch flows
  const fetchFlows = async () => {
    setLoading(true);
    try {
      const response = await axios.get('/flows');
      setFlows(response.data);
    } catch (error) {
      message.error('Failed to fetch flows: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  // View flow details
  const viewFlow = (flow) => {
    Modal.info({
      title: flow.name,
      content: (
        <div>
          <p><strong>Description:</strong> {flow.description || 'No description'}</p>
          <p><strong>Steps:</strong> {flow.steps?.length || 0}</p>
          <p><strong>Status:</strong> {flow.status}</p>
          <p><strong>Created:</strong> {flow.created_at}</p>
          <p><strong>Modified:</strong> {flow.modified_at}</p>
        </div>
      ),
      width: 600,
    });
  };

  // Edit flow
  const editFlow = (flow) => {
    setEditingFlow(flow);
    form.setFieldsValue(flow);
    setModalVisible(true);
  };

  // Duplicate flow
  const duplicateFlow = async (flowId) => {
    try {
      await axios.post(`/flows/${flowId}/duplicate`);
      message.success('Flow duplicated successfully');
      fetchFlows();
    } catch (error) {
      message.error('Failed to duplicate flow: ' + error.message);
    }
  };

  // Delete flow
  const deleteFlow = async (flowId) => {
    try {
      await axios.delete(`/flows/${flowId}`);
      message.success('Flow deleted successfully');
      fetchFlows();
    } catch (error) {
      message.error('Failed to delete flow: ' + error.message);
    }
  };

  // Play flow
  const playFlow = async (flowId) => {
    try {
      message.loading('Starting flow playback...', 1.5);
      // In a real implementation, this would trigger the playback
      setTimeout(() => {
        message.success('Flow playback completed successfully');
      }, 1500);
    } catch (error) {
      message.error('Failed to play flow: ' + error.message);
    }
  };

  // Handle form submission
  const handleFinish = async (values) => {
    try {
      if (editingFlow) {
        // Update existing flow
        await axios.put(`/flows/${editingFlow.id}`, values);
        message.success('Flow updated successfully');
      } else {
        // Create new flow (this would be implemented in a real app)
        message.success('Flow created successfully');
      }
      setModalVisible(false);
      form.resetFields();
      setEditingFlow(null);
      fetchFlows();
    } catch (error) {
      message.error('Operation failed: ' + error.message);
    }
  };

  // Initialize
  useEffect(() => {
    fetchFlows();
  }, []);

  return (
    <div>
      <Title level={2}>Flow Management</Title>
      <p>Manage your recorded automation flows</p>

      <Divider />

      <Card
        title="Recorded Flows"
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => {
              setEditingFlow(null);
              form.resetFields();
              setModalVisible(true);
            }}
          >
            New Flow
          </Button>
        }
      >
        <Table
          dataSource={flows}
          columns={columns}
          loading={loading}
          rowKey="id"
          pagination={{ pageSize: 10 }}
        />
      </Card>

      {/* Flow Edit Modal */}
      <Modal
        title={editingFlow ? "Edit Flow" : "New Flow"}
        open={modalVisible}
        onCancel={() => {
          setModalVisible(false);
          form.resetFields();
          setEditingFlow(null);
        }}
        footer={null}
      >
        <Form
          form={form}
          onFinish={handleFinish}
          layout="vertical"
        >
          <Form.Item
            name="name"
            label="Flow Name"
            rules={[{ required: true, message: 'Please enter flow name' }]}
          >
            <Input placeholder="Enter flow name" />
          </Form.Item>

          <Form.Item
            name="description"
            label="Description"
          >
            <Input.TextArea placeholder="Enter flow description" rows={3} />
          </Form.Item>

          <Form.Item
            name="status"
            label="Status"
          >
            <Input defaultValue="active" readOnly />
          </Form.Item>

          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">
                {editingFlow ? "Update Flow" : "Create Flow"}
              </Button>
              <Button onClick={() => setModalVisible(false)}>Cancel</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default FlowManagement;