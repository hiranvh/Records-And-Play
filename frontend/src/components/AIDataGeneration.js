import React, { useState } from 'react';
import {
  Card, Form, Input, Button, Space, message,
  Typography, Divider, Select, Row, Col,
  Table, Tag, Descriptions
} from 'antd';
import {
  RobotOutlined, PlayCircleOutlined,
  DownloadOutlined, CopyOutlined
} from '@ant-design/icons';
import axios from 'axios';
import ReactJson from 'react-json-view';

const { Title, Text } = Typography;
const { Option } = Select;

const AIDataGeneration = () => {
  const [form] = Form.useForm();
  const [generatedData, setGeneratedData] = useState([]);
  const [loading, setLoading] = useState(false);
  const [originalData, setOriginalData] = useState([
    {
      "type": "input",
      "selector": "#firstName",
      "value": "John",
      "field_name": "first_name"
    },
    {
      "type": "input",
      "selector": "#lastName",
      "value": "Doe",
      "field_name": "last_name"
    },
    {
      "type": "select",
      "selector": "#gender",
      "value": "Male",
      "field_name": "gender"
    },
    {
      "type": "input",
      "selector": "#dob",
      "value": "1990-01-01",
      "field_name": "date_of_birth"
    }
  ]);

  // Generate contextual data
  const generateData = async (values) => {
    setLoading(true);
    try {
      const response = await axios.post('/ai/generate-contextual-data', {
        original_data: originalData,
        count: parseInt(values.count) || 1
      });

      setGeneratedData(response.data);
      message.success(`Generated ${response.data.length} records successfully`);
    } catch (error) {
      message.error('Failed to generate data: ' + error.message);
    } finally {
      setLoading(false);
    }
  };

  // Copy to clipboard
  const copyToClipboard = (text) => {
    navigator.clipboard.writeText(JSON.stringify(text, null, 2));
    message.success('Copied to clipboard');
  };

  // Download as JSON
  const downloadJSON = () => {
    const dataStr = JSON.stringify(generatedData, null, 2);
    const dataUri = 'data:application/json;charset=utf-8,'+ encodeURIComponent(dataStr);

    const exportFileDefaultName = 'generated_test_data.json';

    const linkElement = document.createElement('a');
    linkElement.setAttribute('href', dataUri);
    linkElement.setAttribute('download', exportFileDefaultName);
    linkElement.click();
  };

  // Columns for generated data table
  const columns = [
    {
      title: 'First Name',
      dataIndex: 'first_name',
      key: 'first_name',
    },
    {
      title: 'Last Name',
      dataIndex: 'last_name',
      key: 'last_name',
    },
    {
      title: 'Gender',
      dataIndex: 'gender',
      key: 'gender',
      render: (gender) => (
        <Tag color={gender === 'Male' ? 'blue' : 'pink'}>{gender}</Tag>
      ),
    },
    {
      title: 'Age',
      dataIndex: 'age',
      key: 'age',
      sorter: (a, b) => a.age - b.age,
    },
    {
      title: 'DOB',
      dataIndex: 'dob',
      key: 'dob',
    },
    {
      title: 'Actions',
      key: 'actions',
      render: (_, record) => (
        <Space size="middle">
          <Button
            icon={<CopyOutlined />}
            onClick={() => copyToClipboard(record)}
            size="small"
          >
            Copy
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={2}>
        <RobotOutlined /> AI Data Generation
      </Title>
      <Text type="secondary">Generate contextual test data using AI models</Text>

      <Divider />

      <Row gutter={24}>
        <Col span={12}>
          <Card title="Original Data Pattern">
            <Text type="secondary" style={{ marginBottom: 16, display: 'block' }}>
              This is the pattern that the AI will use to generate new contextual data
            </Text>
            <ReactJson
              src={originalData}
              name="Original Data"
              collapsed={false}
              displayDataTypes={false}
              displayObjectSize={false}
              theme="monokai"
            />
          </Card>
        </Col>

        <Col span={12}>
          <Card title="Generation Settings">
            <Form
              form={form}
              onFinish={generateData}
              layout="vertical"
            >
              <Form.Item
                name="count"
                label="Number of Records"
                initialValue="5"
              >
                <Select>
                  <Option value="1">1 Record</Option>
                  <Option value="5">5 Records</Option>
                  <Option value="10">10 Records</Option>
                  <Option value="25">25 Records</Option>
                  <Option value="50">50 Records</Option>
                </Select>
              </Form.Item>

              <Form.Item
                name="mode"
                label="Generation Mode"
                initialValue="contextual"
              >
                <Select>
                  <Option value="contextual">Contextual (maintain relationships)</Option>
                  <Option value="random">Random (no relationships)</Option>
                  <Option value="mixed">Mixed (some relationships)</Option>
                </Select>
              </Form.Item>

              <Form.Item>
                <Space>
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    htmlType="submit"
                    loading={loading}
                  >
                    Generate Data
                  </Button>
                </Space>
              </Form.Item>
            </Form>
          </Card>
        </Col>
      </Row>

      {generatedData.length > 0 && (
        <>
          <Divider />
          <Card
            title="Generated Test Data"
            extra={
              <Space>
                <Button
                  icon={<DownloadOutlined />}
                  onClick={downloadJSON}
                >
                  Download JSON
                </Button>
                <Button
                  icon={<CopyOutlined />}
                  onClick={() => copyToClipboard(generatedData)}
                >
                  Copy All
                </Button>
              </Space>
            }
          >
            <Table
              dataSource={generatedData}
              columns={columns}
              rowKey={(record, index) => index}
              pagination={{ pageSize: 10 }}
              scroll={{ x: 'max-content' }}
            />
          </Card>

          <Divider />
          <Card title="Raw Data Preview">
            <ReactJson
              src={generatedData}
              name="Generated Data"
              collapsed={false}
              displayDataTypes={false}
              displayObjectSize={false}
              theme="monokai"
            />
          </Card>
        </>
      )}
    </div>
  );
};

export default AIDataGeneration;