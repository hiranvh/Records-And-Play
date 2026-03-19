import React, { useState } from 'react';
import {
  Card, Form, Select, Button, Space, message,
  Typography, Divider, Progress, Radio,
  Descriptions, Tag, List, Collapse
} from 'antd';
import {
  PlayCircleOutlined, PauseOutlined,
  ReloadOutlined, RocketOutlined,
  SettingOutlined, BarChartOutlined
} from '@ant-design/icons';
import axios from 'axios';

const { Title, Text } = Typography;
const { Option } = Select;
const { Panel } = Collapse;

const PlaybackControl = () => {
  const [form] = Form.useForm();
  const [progress, setProgress] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [selectedFlow, setSelectedFlow] = useState(null);
  const [playbackMode, setPlaybackMode] = useState('ai-enhanced');
  const [executionLog, setExecutionLog] = useState([]);

  // Mock flows data
  const mockFlows = [
    { id: 1, name: 'Login Flow', steps: 5 },
    { id: 2, name: 'Registration Flow', steps: 12 },
    { id: 3, name: 'Profile Update Flow', steps: 8 },
    { id: 4, name: 'Payment Flow', steps: 15 },
  ];

  // Simulate playback
  const startPlayback = async (values) => {
    if (!values.flowId) {
      message.error('Please select a flow to play');
      return;
    }

    setIsPlaying(true);
    setProgress(0);
    setExecutionLog([]);

    // Add initial log entry
    const initialLog = {
      timestamp: new Date().toLocaleTimeString(),
      message: `Starting playback of "${mockFlows.find(f => f.id === values.flowId).name}"`,
      status: 'info'
    };
    setExecutionLog([initialLog]);

    // Simulate progress
    const interval = setInterval(() => {
      setProgress(prev => {
        const newProgress = prev + 10;
        if (newProgress >= 100) {
          clearInterval(interval);
          setIsPlaying(false);

          // Add completion log entry
          const completionLog = {
            timestamp: new Date().toLocaleTimeString(),
            message: 'Playback completed successfully',
            status: 'success'
          };
          setExecutionLog(prevLogs => [...prevLogs, completionLog]);

          message.success('Playback completed successfully');
          return 100;
        }
        return newProgress;
      });
    }, 500);

    // Simulate log entries during playback
    setTimeout(() => {
      const logEntries = [
        { timestamp: new Date().toLocaleTimeString(), message: 'Navigating to login page', status: 'info' },
        { timestamp: new Date().toLocaleTimeString(), message: 'Entering username', status: 'info' },
        { timestamp: new Date().toLocaleTimeString(), message: 'Entering password', status: 'info' },
        { timestamp: new Date().toLocaleTimeString(), message: 'Clicking login button', status: 'info' },
        { timestamp: new Date().toLocaleTimeString(), message: 'Verifying successful login', status: 'success' },
      ];
      setExecutionLog(prevLogs => [...prevLogs, ...logEntries]);
    }, 1000);
  };

  // Reset playback
  const resetPlayback = () => {
    setProgress(0);
    setIsPlaying(false);
    setExecutionLog([]);
    form.resetFields();
  };

  // Get flow details
  const getFlowDetails = (flowId) => {
    return mockFlows.find(flow => flow.id === flowId);
  };

  return (
    <div>
      <Title level={2}>
        <PlayCircleOutlined /> Playback Control
      </Title>
      <Text type="secondary">Execute and monitor your recorded automation flows</Text>

      <Divider />

      <Row gutter={24}>
        <Col span={16}>
          <Card title="Playback Configuration">
            <Form
              form={form}
              onFinish={startPlayback}
              layout="vertical"
            >
              <Form.Item
                name="flowId"
                label="Select Flow"
                rules={[{ required: true, message: 'Please select a flow' }]}
              >
                <Select
                  placeholder="Choose a recorded flow"
                  onChange={(value) => setSelectedFlow(getFlowDetails(value))}
                >
                  {mockFlows.map(flow => (
                    <Option key={flow.id} value={flow.id}>
                      {flow.name} ({flow.steps} steps)
                    </Option>
                  ))}
                </Select>
              </Form.Item>

              <Form.Item
                name="playbackMode"
                label="Playback Mode"
                initialValue="ai-enhanced"
              >
                <Radio.Group
                  onChange={(e) => setPlaybackMode(e.target.value)}
                  value={playbackMode}
                >
                  <Radio.Button value="exact">Exact Replay</Radio.Button>
                  <Radio.Button value="ai-enhanced">AI-Enhanced</Radio.Button>
                  <Radio.Button value="hybrid">Hybrid</Radio.Button>
                  <Radio.Button value="standard">Standard Data</Radio.Button>
                </Radio.Group>
              </Form.Item>

              {selectedFlow && (
                <Card size="small" style={{ marginBottom: 16 }}>
                  <Descriptions title="Flow Details" column={2} size="small">
                    <Descriptions.Item label="Name">{selectedFlow.name}</Descriptions.Item>
                    <Descriptions.Item label="Steps">{selectedFlow.steps}</Descriptions.Item>
                    <Descriptions.Item label="Mode">
                      <Tag color={playbackMode === 'ai-enhanced' ? 'blue' : 'default'}>
                        {playbackMode.replace('-', ' ').toUpperCase()}
                      </Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="Status">
                      <Tag color="green">Ready</Tag>
                    </Descriptions.Item>
                  </Descriptions>
                </Card>
              )}

              <Form.Item>
                <Space>
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    htmlType="submit"
                    loading={isPlaying}
                  >
                    {isPlaying ? 'Playing...' : 'Start Playback'}
                  </Button>
                  <Button
                    icon={<ReloadOutlined />}
                    onClick={resetPlayback}
                  >
                    Reset
                  </Button>
                </Space>
              </Form.Item>
            </Form>
          </Card>
        </Col>

        <Col span={8}>
          <Card title="Playback Progress">
            <div style={{ textAlign: 'center', marginBottom: 24 }}>
              <Progress
                type="circle"
                percent={progress}
                status={isPlaying ? "active" : progress === 100 ? "success" : "normal"}
                width={120}
              />
              <div style={{ marginTop: 16 }}>
                <Text strong>
                  {isPlaying ? 'Executing...' : progress === 100 ? 'Completed' : 'Ready'}
                </Text>
              </div>
            </div>

            <Divider />

            <Descriptions title="Statistics" column={1} size="small">
              <Descriptions.Item label="Success Rate">
                <Text type="success">98%</Text>
              </Descriptions.Item>
              <Descriptions.Item label="Avg. Execution Time">
                <Text>2.3s per step</Text>
              </Descriptions.Item>
              <Descriptions.Item label="Last Run">
                <Text type="secondary">2 minutes ago</Text>
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>

      {executionLog.length > 0 && (
        <>
          <Divider />
          <Card title="Execution Log">
            <List
              dataSource={executionLog}
              renderItem={(item) => (
                <List.Item>
                  <List.Item.Meta
                    avatar={
                      item.status === 'success' ?
                      <Tag color="success">✓</Tag> :
                      item.status === 'error' ?
                      <Tag color="error">✗</Tag> :
                      <Tag color="processing">⋯</Tag>
                    }
                    title={item.timestamp}
                    description={item.message}
                  />
                </List.Item>
              )}
            />
          </Card>
        </>
      )}

      <Divider />
      <Collapse ghost>
        <Panel header="Advanced Playback Settings" key="1">
          <Card title="Advanced Configuration" size="small">
            <Descriptions column={2} size="small">
              <Descriptions.Item label="Speed">Normal (1x)</Descriptions.Item>
              <Descriptions.Item label="Retries">3 attempts</Descriptions.Item>
              <Descriptions.Item label="Timeout">30 seconds</Descriptions.Item>
              <Descriptions.Item label="Parallel Execution">Disabled</Descriptions.Item>
              <Descriptions.Item label="Screenshot on Fail">Enabled</Descriptions.Item>
              <Descriptions.Item label="Video Recording">Disabled</Descriptions.Item>
            </Descriptions>
          </Card>
        </Panel>
      </Collapse>
    </div>
  );
};

export default PlaybackControl;