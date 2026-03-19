import React, { useState, useEffect } from 'react';
import {
  Card, Row, Col, Button, Statistic, Space, Tag, Typography,
  Divider, Input, message, Descriptions, Badge, Modal, Form
} from 'antd';
import {
  PlayCircleOutlined,
  StopOutlined,
  FileTextOutlined,
  RocketOutlined,
  CloudUploadOutlined,
  DesktopOutlined,
  ClockCircleOutlined
} from '@ant-design/icons';
import axios from 'axios';

const { Title, Text } = Typography;
const { TextArea } = Input;

const RecordingDashboard = () => {
  const [isRecording, setIsRecording] = useState(false);
  const [recordingTime, setRecordingTime] = useState(0);
  const [sessionName, setSessionName] = useState('');
  const [flows, setFlows] = useState([]);
  const [stats, setStats] = useState({
    totalFlows: 0,
    activeSessions: 0,
    successfulTests: 0,
    failedTests: 0
  });
  const [viewModalVisible, setViewModalVisible] = useState(false);
  const [editModalVisible, setEditModalVisible] = useState(false);
  const [currentFlow, setCurrentFlow] = useState(null);
  const [editForm, setEditForm] = useState({ name: '' });

  // Timer effect for recording
  useEffect(() => {
    let timer;
    if (isRecording) {
      timer = setInterval(() => {
        setRecordingTime(prev => prev + 1);
      }, 1000);
    }
    return () => clearInterval(timer);
  }, [isRecording]);

  // Format time for display
  const formatTime = (seconds) => {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  // Start recording
  const startRecording = async () => {
    if (!sessionName.trim()) {
      message.error('Please enter a session name');
      return;
    }

    try {
      const response = await axios.post('/record/start', {
        session_name: sessionName
      });
      setIsRecording(true);
      setRecordingTime(0);
      message.success('Recording started successfully');
    } catch (error) {
      message.error('Failed to start recording: ' + error.message);
    }
  };

  // Stop recording
  const stopRecording = async () => {
    try {
      // In a real implementation, you would pass the actual recording ID
      const response = await axios.post('/record/stop/1');
      setIsRecording(false);
      setRecordingTime(0);
      setSessionName('');
      message.success('Recording stopped and saved');

      // Refresh flows
      fetchFlows();
    } catch (error) {
      message.error('Failed to stop recording: ' + error.message);
    }
  };

  // Fetch flows
  const fetchFlows = async () => {
    try {
      const response = await axios.get('/flows');
      setFlows(response.data);
      setStats({
        ...stats,
        totalFlows: response.data.length
      });
    } catch (error) {
      message.error('Failed to fetch flows: ' + error.message);
    }
  };

  const handleDeleteFlow = async (id) => {
    try {
      await axios.delete(`/flows/${id}`);
      message.success('Flow deleted successfully');
      fetchFlows();
    } catch (error) {
      message.error('Failed to delete flow: ' + error.message);
    }
  };

  const handleViewFlow = async (flow) => {
    try {
      const response = await axios.get(`/flows/${flow.id}`);
      setCurrentFlow(response.data);
      setViewModalVisible(true);
    } catch (error) {
      message.error('Failed to fetch flow details: ' + error.message);
    }
  };

  const handleEditFlow = (flow) => {
    setCurrentFlow(flow);
    setEditForm({ name: flow.name });
    setEditModalVisible(true);
  };

  const submitEditFlow = async () => {
    try {
      await axios.put(`/flows/${currentFlow.id}`, { name: editForm.name });
      message.success('Flow updated successfully');
      setEditModalVisible(false);
      fetchFlows();
    } catch (error) {
      message.error('Failed to update flow: ' + error.message);
    }
  };

  // Initialize
  useEffect(() => {
    fetchFlows();
  }, []);

  return (
    <div>
      <Title level={2}>
        <DesktopOutlined /> Automation Dashboard
      </Title>
      <Text type="secondary">Manage your recording and playback automation workflows</Text>

      <Divider />

      {/* Recording Controls */}
      <Card title="Recording Session" className="dashboard-card">
        <Row gutter={16}>
          <Col span={16}>
            <Space direction="vertical" style={{ width: '100%' }}>
              <Input
                placeholder="Enter session name"
                value={sessionName}
                onChange={(e) => setSessionName(e.target.value)}
                disabled={isRecording}
              />
              <Space>
                {!isRecording ? (
                  <Button
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    onClick={startRecording}
                    size="large"
                  >
                    Start Recording
                  </Button>
                ) : (
                  <Button
                    type="primary"
                    danger
                    icon={<StopOutlined />}
                    onClick={stopRecording}
                    size="large"
                  >
                    Stop Recording
                  </Button>
                )}
                <Tag icon={<ClockCircleOutlined />} color={isRecording ? "red" : "default"}>
                  {isRecording ? (
                    <span><span className="recording-indicator"></span>{formatTime(recordingTime)}</span>
                  ) : 'Not recording'}
                </Tag>
              </Space>
            </Space>
          </Col>
          <Col span={8}>
            <Card size="small" title="Current Session">
              <Descriptions column={1} size="small">
                <Descriptions.Item label="Status">
                  <Badge status={isRecording ? "processing" : "default"} text={isRecording ? "Recording" : "Idle"} />
                </Descriptions.Item>
                <Descriptions.Item label="Session Name">
                  {sessionName || 'None'}
                </Descriptions.Item>
                <Descriptions.Item label="Steps Recorded">
                  {isRecording ? Math.floor(recordingTime / 10) : 0}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          </Col>
        </Row>
      </Card>

      {/* Statistics */}
      <Row gutter={16} style={{ marginBottom: 24 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="Total Flows"
              value={stats.totalFlows}
              prefix={<FileTextOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Active Sessions"
              value={stats.activeSessions}
              prefix={<PlayCircleOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Successful Tests"
              value={stats.successfulTests}
              prefix={<RocketOutlined />}
              valueStyle={{ color: '#3f8600' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="Failed Tests"
              value={stats.failedTests}
              prefix={<StopOutlined />}
              valueStyle={{ color: '#cf1322' }}
            />
          </Card>
        </Col>
      </Row>

      {/* Recent Flows */}
      <Card title="Recent Flows" extra={<a onClick={(e) => { e.preventDefault(); message.info('All flows are currently displayed.'); }}>View All</a>}>
        {flows.slice(0, 5).map((flow) => (
          <Card
            key={flow.id}
            size="small"
            title={flow.name}
            extra={<Tag color="blue">ID: {flow.id}</Tag>}
            style={{ marginBottom: 16 }}
          >
            <Descriptions size="small" column={2}>
              <Descriptions.Item label="Status">
                <Tag color={flow.status === 'active' ? 'green' : 'default'}>
                  {flow.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Steps">{flow.steps?.length || 0}</Descriptions.Item>
              <Descriptions.Item label="Created">{flow.created_at}</Descriptions.Item>
              <Descriptions.Item label="Modified">{flow.modified_at}</Descriptions.Item>
            </Descriptions>
            <Space style={{ marginTop: 12 }}>
              <Button size="small" type="primary" onClick={() => handleViewFlow(flow)}>View</Button>
              <Button size="small" onClick={() => handleEditFlow(flow)}>Edit</Button>
              <Button size="small" danger onClick={() => handleDeleteFlow(flow.id)}>Delete</Button>
            </Space>
          </Card>
        ))}
        {flows.length === 0 && (
          <Text type="secondary">No flows recorded yet. Start recording to create your first flow.</Text>
        )}
      </Card>

      <Modal
        title="View Flow"
        open={viewModalVisible}
        onCancel={() => setViewModalVisible(false)}
        footer={[<Button key="close" onClick={() => setViewModalVisible(false)}>Close</Button>]}
      >
        {currentFlow && (
          <div>
            <p><strong>Name:</strong> {currentFlow.name}</p>
            <p><strong>Description:</strong> {currentFlow.description || 'N/A'}</p>
            <p><strong>Status:</strong> {currentFlow.status}</p>
            <p><strong>Steps:</strong></p>
            <pre style={{ background: '#f5f5f5', padding: '10px', borderRadius: '4px' }}>
              {JSON.stringify(currentFlow.steps, null, 2)}
            </pre>
          </div>
        )}
      </Modal>

      <Modal
        title="Edit Flow"
        open={editModalVisible}
        onOk={submitEditFlow}
        onCancel={() => setEditModalVisible(false)}
      >
        <Form layout="vertical">
          <Form.Item label="Flow Name">
            <Input 
              value={editForm.name} 
              onChange={(e) => setEditForm({...editForm, name: e.target.value})} 
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default RecordingDashboard;